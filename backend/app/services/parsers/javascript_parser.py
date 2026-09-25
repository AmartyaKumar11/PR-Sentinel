"""JavaScript dependency graph. Same walker as TypeScript, plus require()."""

from app.services.parsers.typescript_parser import JsFamilyParser


class JavaScriptParser(JsFamilyParser):
    grammar = "javascript"
    allow_require = True
