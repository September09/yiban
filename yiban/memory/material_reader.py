"""Bounded local extraction worker. No network, model invocation or database access."""
import csv
import io
import json
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET

def read_file(path, suffix):
    raw = Path(path).read_bytes()
    warnings = []
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 20_000_000:
                raise ValueError('Word 解压后过大，请拆分资料。')
            document = archive.read('word/document.xml')
            if b'<!DOCTYPE' in document or b'<!ENTITY' in document:
                raise ValueError('不支持含实体声明的文档。')
            root = ET.fromstring(document)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in root.iter(ns+'p'))
        warnings.append('Word 读取正文及表格文字；图片、页眉页脚、批注和排版不包含在结果中。')
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted: raise ValueError('请先解除 PDF 密码后再上传。')
        if len(reader.pages) > 20: raise ValueError('PDF 每份最多 20 页，请拆分后上传。')
        pages = []
        for i, page in enumerate(reader.pages):
            content = page.extract_text() or ''
            if not content.strip(): warnings.append(f'第 {i+1} 页没有可提取文字，请转为截图在 Mac 识别或手动补充。')
            pages.append(f'【第 {i+1} 页】\n{content}')
        text = '\n\n'.join(pages)
    else:
        if b'\x00' in raw and not raw.startswith((b'\xff\xfe',b'\xfe\xff')):
            raise ValueError('文件不是支持的文字格式。')
        try: text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
        except UnicodeDecodeError:
            try: text = raw.decode('gb18030')
            except UnicodeDecodeError: raise ValueError('文字编码无法识别，请另存为 UTF-8。') from None
        if suffix == '.json':
            text = json.dumps(json.loads(text),ensure_ascii=False,indent=2)
            warnings.append('JSON 保留字段和原始记录，请在原文预览中核对发言者及时间。')
        if suffix == '.csv':
            text = '\n'.join(' | '.join(row) for row in csv.reader(io.StringIO(text)))
    if not text.strip(): raise ValueError('没有读取到文字，请粘贴文字或上传清晰截图。')
    if len(text) > 30000: raise ValueError('文字超过 30000 字，请拆分资料；本次没有截断导入。')
    return {'text':text.strip(),'warnings':warnings}

if __name__ == '__main__':
    try:
        print(json.dumps(read_file(sys.argv[1],sys.argv[2]),ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else '文件格式损坏或不受支持，请转换后重试。'
        print(json.dumps({'error':message},ensure_ascii=False))
        sys.exit(1)
