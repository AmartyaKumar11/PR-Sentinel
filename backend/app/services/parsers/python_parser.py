"""Python backend: the existing ast module parser, unchanged."""

from app.services.ast_parser import parse_file as ast_parse_file
from app.services.parsers.base import LanguageParser


class PythonParser(LanguageParser):
    def parse_file(self, file_path, source_code):
        return ast_parse_file(file_path, source_code)
