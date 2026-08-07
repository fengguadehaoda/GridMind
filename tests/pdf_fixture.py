"""测试用最小合法 PDF 构造工具（KB Upload · P1: PDF 上传支持）。

pypdf 只能**读** PDF、不能写文本页（写入文本需要 reportlab 等库，本项目未安装），
因此测试用**手写最小 PDF 字节**：单/多页 A4 + Helvetica 标准字体 + ASCII 文本。
标准 14 字体无需嵌入（CJK 需嵌入字体过于复杂，故测试文本用英文）；
xref 偏移按字节精确生成，保证 pypdf 可解析。

**用法**::

    from pdf_fixture import build_min_pdf
    pdf_bytes = build_min_pdf("Transformer temperature range: 40-70 C")

作者：寇豆码（工程师）
"""

from __future__ import annotations


def build_min_pdf(texts: list[str] | str = "Transformer temperature range: 40-70 C") -> bytes:
    """构造最小合法 PDF（Helvetica 标准字体，每页一行给定 ASCII 文本）。

    Args:
        texts: 页文本列表（每项一页）；传 ``str`` 视为单页。空串表示空白页。

    Returns:
        合法 PDF 字节，可被 ``pypdf.PdfReader`` 解析并 ``extract_text()`` 提取。
    """
    if isinstance(texts, str):
        texts = [texts]
    n_pages = len(texts)

    # 对象编号分配：
    #   1 Catalog / 2 Pages / 3..(2+n_pages) Page /
    #   (3+n_pages)..(2+2*n_pages) ContentStream / 末位 Font
    page_nums = [3 + i for i in range(n_pages)]
    content_nums = [3 + n_pages + i for i in range(n_pages)]
    font_num = 3 + 2 * n_pages

    kids = " ".join(f"{p} 0 R" for p in page_nums)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii"),
    ]
    for i in range(n_pages):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_nums[i]} 0 R "
                f"/Resources << /Font << /F1 {font_num} 0 R >> >> >>"
            ).encode("ascii")
        )
    for text in texts:
        # PDF 字符串转义：反斜杠 / 圆括号
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        stream = b"stream\n" + content + b"\nendstream"
        objects.append(
            b"<< /Length " + str(len(content)).encode("ascii") + b" >>\n" + stream
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"

    xref_pos = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {n} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)
