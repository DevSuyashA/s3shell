import platform


FILE_ICON_MAP = {
    '.txt': '📄', '.md': '📄', '.pdf': '📄', '.log': '📄',
    '.jpg': '🖼', '.jpeg': '🖼', '.png': '🖼', '.gif': '🖼', '.svg': '🖼',
    '.py': '🐍', '.js': '🟨', '.html': '🌐', '.css': '🎨', '.json': '⚙️', '.yaml': '⚙️', '.yml': '⚙️',
    '.zip': '📦', '.gz': '📦', '.tar': '📦', '.rar': '📦', '.7z': '📦',
    '.mp3': '🎵', '.wav': '🎵', '.mp4': '🎥', '.mov': '🎥', '.avi': '🎥',
    '.csv': '📊', '.xls': '📊', '.xlsx': '📊', '.doc': '📝', '.docx': '📝',
    '': '📄',
}


def get_file_icon(extension):
    return FILE_ICON_MAP.get(extension, '📄')


def human_readable_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0 or unit == 'TB':
            break
        size /= 1024.0
    return f"{size:.1f} {unit}"


def format_dir_entry(dir_name):
    icon = '📁 ' if platform.system() != 'Windows' else ''
    return f"{icon}{dir_name}/"


def format_file_entry(file_info, detailed=False):
    icon = get_file_icon(file_info['extension'])
    if not detailed:
        return f"{icon} {file_info['name']}"
    else:
        date_str = file_info['last_modified'].strftime('%Y-%m-%d %H:%M')
        size_str = human_readable_size(file_info['size'])
        return f"{icon} {date_str} {size_str:>9} {file_info['name']}"
