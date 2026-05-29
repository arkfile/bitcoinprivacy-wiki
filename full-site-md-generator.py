#!/usr/bin/env python3
import os
import re
import yaml
from datetime import datetime

# Set up paths
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
MKDOCS_YML = os.path.join(WORK_DIR, 'mkdocs.yml')
DOCS_DIR = os.path.join(WORK_DIR, 'docs')
OUTPUT_FILE = os.path.join(WORK_DIR, 'full-site.md')

def create_backup():
    if not os.path.exists(OUTPUT_FILE):
        return

    today_str = datetime.now().strftime('%Y%m%d')
    backup_path = os.path.join(WORK_DIR, f'full-site-backup-{today_str}.md')
    
    # If a backup for today already exists, append active serial increment (e.g. _1, _2)
    counter = 1
    while os.path.exists(backup_path):
        backup_path = os.path.join(WORK_DIR, f'full-site-backup-{today_str}_{counter}.md')
        counter += 1

    try:
        import shutil
        shutil.copy2(OUTPUT_FILE, backup_path)
        print(f"Backed up existing 'full-site.md' to '{os.path.basename(backup_path)}'")
    except Exception as e:
        print(f"Error creating backup: {e}")

def parse_nav_item(item):
    """
    Recursively extracts markdown files from a nav entry in mkdocs.yml
    Yields tuple of (nested_hierarchy_list, file_path)
    """
    if isinstance(item, str):
        # Simply a filepath like "glossary.md"
        yield ([], item)
    elif isinstance(item, dict):
        for key, val in item.items():
            if isinstance(val, str):
                yield ([key], val)
            elif isinstance(val, list):
                for sub_item in val:
                    for sub_hierarchy, path in parse_nav_item(sub_item):
                        yield ([key] + sub_hierarchy, path)
            else:
                pass # Unrecognized format

def extract_ordered_files():
    """
    Reads mkdocs.yml and returns a list of dictionaries with page metadata in nav order.
    """
    if not os.path.exists(MKDOCS_YML):
        print(f"Error: mkdocs.yml not found at {MKDOCS_YML}")
        return []

    # Handle !python/name and !!python/name tags that aren't natively in SafeLoader
    class SafeLoaderIgnoreUnknown(yaml.SafeLoader):
        def ignore_unknown(self, node):
            return node.value

    # Register constructors for custom python tags to prevent constructor errors
    SafeLoaderIgnoreUnknown.add_multi_constructor('tag:yaml.org,2002:python/name:', lambda loader, suffix, node: node.value)
    # Also ignore generic unknown yaml tags
    SafeLoaderIgnoreUnknown.add_constructor(None, lambda loader, node: node.value if hasattr(node, 'value') else None)

    with open(MKDOCS_YML, 'r', encoding='utf-8') as f:
        config = yaml.load(f, Loader=SafeLoaderIgnoreUnknown)

    nav = config.get('nav', [])
    files = []
    
    for item in nav:
        for hierarchy, path in parse_nav_item(item):
            full_path = os.path.join(DOCS_DIR, path)
            if os.path.exists(full_path):
                files.append({
                    'relative_path': path,
                    'full_path': full_path,
                    'hierarchy': hierarchy
                })
            else:
                print(f"Warning: File defined in nav does not exist: {full_path}")
    return files

def strip_front_matter(content):
    """
    Strips raw Jekyll/MkDocs front-matter blocks from markdown text.
    Handles blocks wrapped with --- at the absolute beginning of the file.
    """
    # Front-matter must start at the very first character of the file
    pattern = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
    match = pattern.match(content)
    if match:
        return content[match.end():]
    return content

def clean_markdown_content(content, file_rel_path, link_anchor_map):
    """
    Cleans up markdown content for a beautiful local VSCodium presentation:
    1. Finds and rewrites relative image paths to a project-root-relative path.
    2. Strips custom theme annotations, specifically MkDocs Material icons like
       :material-check:{ .lg .middle } to prevent ugly rendering in VSCodium.
    3. Rewrites absolute/relative link paths pointing to internal markdown files
       (e.g., [Get Started](getting-started/privacy.md)) to point directly to
       anchor headers inside the merged document so VSCodium links work flawlessly!
    """
    file_dir = os.path.dirname(file_rel_path) # e.g., 'getting-started' or 'techniques/coinjoin'
    
    # 1. Update markdown image paths
    def replace_markdown_image(match):
        alt_text = match.group(1)
        original_path = match.group(2)
        # Split attributes if they have them like { loading=lazy width="250" } or #only-dark
        path_without_attrs = original_path
        attrs = ""
        for marker in ('{', '#'):
            if marker in path_without_attrs:
                parts = path_without_attrs.split(marker, 1)
                path_without_attrs = parts[0]
                attrs = marker + parts[1]
                break

        # Resolve relative image link to root path
        resolved_path = os.path.normpath(os.path.join('docs', file_dir, path_without_attrs))
        
        # Normpath can produce backslashes on Windows; make sure they are forward slashes for cross-platform Markdown
        resolved_path = resolved_path.replace('\\', '/')
        
        return f'![{alt_text}]({resolved_path}{attrs})'

    # Match ![Alt Text](img_url)
    markdown_image_pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    content = markdown_image_pattern.sub(replace_markdown_image, content)
    
    # Match HTML Style <img> tag: <img src="relative_path" ...>
    def replace_html_image(match):
        full_tag = match.group(0)
        src_match = re.search(r'src=["\']([^"\']+)["\']', full_tag)
        if src_match:
            original_src = src_match.group(1)
            # Only resolve if relative, ignoring absolute URLs
            if not (original_src.startswith('http://') or original_src.startswith('https://') or original_src.startswith('//')):
                resolved_src = os.path.normpath(os.path.join('docs', file_dir, original_src)).replace('\\', '/')
                # Replace original src with resolved absolute-relative src
                new_tag = full_tag.replace(original_src, resolved_src)
                return new_tag
        return full_tag

    html_image_pattern = re.compile(r'<img\s+[^>]*src=["\'][^"\']+["\'][^>]*>')
    content = html_image_pattern.sub(replace_html_image, content)

    # 2. Strip Material icons/decorations like :material-icon-name:{ .attributes }
    # Look for patterns like ":material-home-lock:{ .lg .middle } " and strip them
    icon_pattern = re.compile(r':material-[a-zA-Z0-9_-]+:(?:\{\s*\.[a-zA-Z0-9_.-]+\s*\.\w+\s*\})?\s*')
    content = icon_pattern.sub('', content)

    # 3. Rewrite relative page links (e.g. `[Basics](getting-started/why-care...md)`) to local internal anchors
    def replace_markdown_link(match):
        link_text = match.group(1)
        original_href = match.group(2)
        
        # Only rewrite relative links, ignore absolute URL protocols
        if any(original_href.startswith(p) for p in ('http://', 'https://', 'mailto:', 'ftp://', '//')):
            return match.group(0)

        # Split off custom hashtags if there are any
        path_part = original_href
        hash_part = ""
        if '#' in original_href:
            parts = original_href.split('#', 1)
            path_part = parts[0]
            hash_part = parts[1]

        # Resolve path_part relative to the current file's folder to normalize it
        resolved_link_path = os.path.normpath(os.path.join(file_dir, path_part)).replace('\\', '/')

        # Check if the resolved file link exists in our link-to-anchor mapping
        if resolved_link_path in link_anchor_map:
            local_anchor = link_anchor_map[resolved_link_path]
            if hash_part:
                # If there's already a hash_part, it usually references page sections.
                # In standard markdown generation, section titles are preserved, but to be completely safe,
                # we leap to the main section heading, or concatenate.
                # Let's cleanly resolve to the section heading for seamless navigation.
                return f'[{link_text}]({local_anchor})'
            return f'[{link_text}]({local_anchor})'

        # Fallback if the path points directly to something else in docs
        return match.group(0)

    # Match standard markdown links [text](href) but NOT images ![text](href) (which have a preceding !)
    # Regex explains: lookbehind for NOT ! then [text](href)
    markdown_link_pattern = re.compile(r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)')
    content = markdown_link_pattern.sub(replace_markdown_link, content)

    # Convert MkDocs Custom Admonitions blocks (e.g., !!! danger "Title") to standard blockquotes
    content = transform_admonitions(content)

    return content

def transform_admonitions(content):
    """
    Parses MkDocs admonition blocks like:
    !!! warning "Title"
        nested line 1
        nested line 2
    and translates them to beautiful standard Markdown blockquotes:
    > **[WARNING] Title**
    >
    > nested line 1
    > nested line 2
    """
    lines = content.splitlines()
    transformed = []
    i = 0
    num_lines = len(lines)
    
    while i < num_lines:
        line = lines[i]
        # Check if line starts with prompt admonition delimiter !!!
        if line.strip().startswith('!!!'):
            # Parse admonition type and optional title
            match = re.match(r'^\s*!!!\s+([a-zA-Z0-9_-]+)(?:\s+["\']([^"\']+)["\'])?\s*$', line)
            if match:
                adm_type = match.group(1).upper()
                adm_title = match.group(2)
                
                # Combine type and title into block header
                header_text = f"**[{adm_type}] {adm_title}**" if adm_title else f"**[{adm_type}]**"
                transformed.append(f"> {header_text}")
                transformed.append(">")
                
                # Read subsequent nested indented blocks
                i += 1
                indent_level = None
                
                # We consume any lines that are:
                # 1. Blank
                # 2. Indented relative to the start
                while i < num_lines:
                    sub_line = lines[i]
                    if not sub_line.strip():
                        transformed.append(">")
                        i += 1
                        continue
                    
                    # Detect indentation of content
                    current_indent = len(sub_line) - len(sub_line.lstrip())
                    if current_indent > 0:
                        if indent_level is None:
                            indent_level = current_indent
                        # Strip only the admonition nested indentation and prepend blockquote standard formatting
                        stripped_line = sub_line[indent_level:] if sub_line.startswith(' ' * indent_level) else sub_line.lstrip()
                        transformed.append(f"> {stripped_line}")
                        i += 1
                    else:
                        # Non-indented line means we've exited the admonition block
                        break
                continue
            
        transformed.append(line)
        i += 1
        
    return "\n".join(transformed)

def compile_full_site():
    print("Beginning site compilation...")
    create_backup()

    ordered_files = extract_ordered_files()
    if not ordered_files:
        print("Error: No markdown files found to compile.")
        return

    print(f"Found {len(ordered_files)} files to compile in specified nav order.")

    # 1. First Pass: Read files and map each relative_path to its main markdown header slug/anchor.
    # This enables us to rewrite file links to internal anchor jumps dynamically.
    link_anchor_map = {}
    title_pattern = re.compile(r'^#\s+(.+)$', re.MULTILINE)

    for file_info in ordered_files:
        rel_path = file_info['relative_path']
        full_path = file_info['full_path']
        
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        content_no_fm = strip_front_matter(content)
        title_match = title_pattern.search(content_no_fm)
        if title_match:
            title_text = title_match.group(1).strip()
            # Standard markdown slugification format (space->hyphen, lowercase, alphanumeric-only)
            slug = title_text.lower()
            slug = re.sub(r'[^a-z0-9\s-]', '', slug)
            slug = re.sub(r'\s+', '-', slug)
            # Link to the current output file to prevent VSCodium from confusing it as a separate workspace file
            link_anchor_map[rel_path] = f"./full-site.md#{slug}"
        else:
            base_name = os.path.basename(rel_path).replace('.md', '')
            link_anchor_map[rel_path] = f"./full-site.md#{base_name}"

    combined_content = []
    
    # Header for the merged site document - metadata/timestamp removed for pure privacy
    combined_content.append("# BitcoinPrivacy.Wiki - Full Consolidated Documentation\n")
    combined_content.append("> This document is a single-file compilation of all educational resources on Bitcoin privacy from BitcoinPrivacy.Wiki.\n\n---\n")

    current_section = None

    for idx, file_info in enumerate(ordered_files, 1):
        rel_path = file_info['relative_path']
        full_path = file_info['full_path']
        hierarchy = file_info['hierarchy']

        print(f"[{idx}/{len(ordered_files)}] Processing {rel_path}...")

        # Build a beautiful section divider based on the navigation path
        full_nav_title = " / ".join(hierarchy) if hierarchy else "Home"
        
        # Read the file content
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Clean document and pass our link_anchor_map
        content = strip_front_matter(content)
        content = clean_markdown_content(content, rel_path, link_anchor_map)

        # Add section markers so it's beautifully organized
        section_heading = ""
        if full_nav_title != current_section:
            current_section = full_nav_title
            section_heading = f"\n\n\n# SECTION: {full_nav_title}\n"
            section_heading += f"<!-- FILE: {rel_path} -->\n"
            section_heading += "---\n\n"
        else:
            section_heading = f"\n\n\n<!-- FILE: {rel_path} -->\n"
            section_heading += "---\n\n"

        combined_content.append(section_heading + content)

    # Write out the consolidated markdown
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.writelines(combined_content)

    print(f"\nSuccessfully compiled '{os.path.basename(OUTPUT_FILE)}'!")

if __name__ == '__main__':
    compile_full_site()
