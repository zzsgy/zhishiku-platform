"""Immutable uploads, bounded paths, and atomic derived-file publication."""
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

TEXT_EXTENSIONS = {'.txt', '.text', '.md', '.markdown', '.csv', '.tsv', '.json',
                   '.log', '.xml', '.yaml', '.yml', '.py', '.js', '.css', '.sql',
                   '.ini', '.cfg', '.rtf', '.html', '.htm'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}
DOCUMENT_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xlsx', '.pptx'}


def inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def bounded_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or '..' in relative.parts:
        raise ValueError('文件路径必须是允许目录内的相对路径')
    path = (root / relative).resolve()
    if not inside(path, root):
        raise ValueError('文件路径超出允许目录')
    return path


def media_path(value):
    path = Path(value)
    if path.is_absolute():
        if not inside(path, settings.MEDIA_ROOT):
            raise ValueError('原件不在媒体目录内')
        return path.resolve()
    return bounded_path(settings.MEDIA_ROOT, value)


def media_url(path):
    rel = media_path(path).relative_to(Path(settings.MEDIA_ROOT).resolve())
    return settings.MEDIA_URL + quote(rel.as_posix(), safe='/')


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content.encode('utf-8') if isinstance(content, str) else content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_file(path, extension):
    with open(path, 'rb') as source:
        head = source.read(4096)
    if head.startswith((b'MZ', b'\x7fELF')):
        raise ValueError('文件内容与允许格式不符')
    if extension == '.pdf' and not head.startswith(b'%PDF-'):
        raise ValueError('文件不是有效 PDF')
    if extension == '.doc' and not head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise ValueError('文件不是有效的旧版 Word 文档；请另存为 DOCX 或 PDF 后上传')
    if extension in {'.docx', '.xlsx', '.pptx'}:
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > 10000 or sum(i.file_size for i in infos) > 256 * 1024**2:
                    raise ValueError('文档解压资源超限')
                marker = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml',
                          '.pptx': 'ppt/presentation.xml'}[extension]
                if marker not in archive.namelist():
                    raise ValueError('文档内容与扩展名不符')
                if any('vbaproject' in i.filename.lower() for i in infos):
                    raise ValueError('不接受包含宏的文档')
        except zipfile.BadZipFile as exc:
            raise ValueError('文档不是有效的 Office 压缩格式') from exc
    elif extension in IMAGE_EXTENSIONS:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(path) as img:
                if img.width * img.height > 40_000_000:
                    raise ValueError('图片超过四千万像素处理上限')
                img.verify()
        except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError('图片无效或解压资源超限') from exc
    elif extension in TEXT_EXTENSIONS and b'\x00' in head and not head.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise ValueError('文本文件包含二进制内容')


def save_upload(upload):
    extension = Path(upload.name).suffix.lower()
    if extension not in TEXT_EXTENSIONS | IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS:
        raise ValueError('不支持的原件格式')
    maximum = settings.UPLOAD_MAX_BYTES
    if getattr(upload, 'size', 0) > maximum:
        raise ValueError('原件超过上传大小限制')
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=root)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, 'wb') as out:
            for chunk in upload.chunks():
                size += len(chunk)
                if size > maximum:
                    raise ValueError('原件超过上传大小限制')
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        validate_file(temporary, extension)
        checksum = digest.hexdigest()
        relative = f'assets/{checksum[:2]}/{checksum}{extension}'
        destination = bounded_path(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # An identical digest can share immutable bytes; display names remain on references.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != checksum:
                raise ValueError('原件哈希冲突，已拒绝覆盖')
        from .models import Asset
        asset, _ = Asset.objects.get_or_create(storage_path=relative, defaults={
            'sha256': checksum, 'size': size, 'original_name': Path(upload.name).name})
        return destination, asset
    finally:
        Path(temporary).unlink(missing_ok=True)
        upload.seek(0)
