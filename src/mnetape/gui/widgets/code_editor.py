"""QScintilla code editor factory with a dark IDE theme and Python syntax highlighting.

Exports a single factory function, create_code_editor(), that returns a fully
configured QsciScintilla instance matching the application's dark colour scheme.
"""

from PyQt6.QtGui import QColor, QFont
from PyQt6.Qsci import QsciScintilla, QsciLexerPython


def create_code_editor(parent=None) -> QsciScintilla:
    """Create a QsciScintilla editor with a dark theme and Python syntax highlighting.

    Configures font, indentation, line-number margins, caret, selection colours,
    and a QsciLexerPython with token-level colour assignments.

    Args:
        parent: Optional parent QWidget.

    Returns:
        A fully configured QsciScintilla instance ready to embed in a layout.
    """
    editor = QsciScintilla(parent)

    font = QFont("Consolas", 11)
    font.setFixedPitch(True)
    editor.setFont(font)

    editor.setUtf8(True)
    editor.setTabWidth(4)
    editor.setIndentationsUseTabs(False)
    editor.setAutoIndent(True)
    editor.setWrapMode(QsciScintilla.WrapMode.WrapNone)
    editor.setEolMode(QsciScintilla.EolMode.EolUnix)

    # Line numbers
    editor.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
    editor.setMarginWidth(0, "0000")
    editor.setMarginsForegroundColor(QColor("#858585"))
    editor.setMarginsBackgroundColor(QColor("#1E1E1E"))

    editor.setPaper(QColor("#1E1E1E"))
    editor.setColor(QColor("#A9B7C6"))
    editor.setCaretForegroundColor(QColor("#A9B7C6"))
    editor.setCaretLineVisible(True)
    editor.setCaretLineBackgroundColor(QColor("#2B2B2B"))
    editor.setSelectionBackgroundColor(QColor("#214283"))
    editor.setSelectionForegroundColor(QColor("#A9B7C6"))

    # Lexer
    lexer = QsciLexerPython(editor)
    lexer.setFont(font)
    lexer.setDefaultPaper(QColor("#1E1E1E"))
    lexer.setDefaultColor(QColor("#A9B7C6"))

    # Set background and font for all styles
    for i in range(128):
        lexer.setPaper(QColor("#1E1E1E"), i)
        lexer.setFont(font, i)

    # Token colors
    P = QsciLexerPython
    str_color = QColor("#6AAB73")
    for color, styles in [
        (QColor("#A9B7C6"), [P.Default, P.Operator, P.Identifier]),
        (QColor("#7A7E85"), [P.Comment, P.CommentBlock]),
        (QColor("#2AACB8"), [P.Number]),
        (str_color,         [P.SingleQuotedString, P.DoubleQuotedString,
                             P.TripleSingleQuotedString, P.TripleDoubleQuotedString]),
        (QColor("#CF8E6D"), [P.Keyword]),
        (QColor("#56A8F5"), [P.ClassName, P.FunctionMethodName, P.Decorator]),
    ]:
        for style in styles:
            lexer.setColor(color, style)

    # Version-dependent styles
    for color, names in [
        (str_color,         ["SingleQuotedFString", "DoubleQuotedFString",
                             "TripleSingleQuotedFString", "TripleDoubleQuotedFString"]),
        (QColor("#E06C75"), ["UnclosedString"]),
    ]:
        for name in names:
            if (style_id := getattr(P, name, None)) is not None:
                lexer.setColor(color, style_id)

    # Bold keywords
    kw_font = QFont(font)
    kw_font.setBold(True)
    lexer.setFont(kw_font, P.Keyword)

    editor.setLexer(lexer)
    return editor