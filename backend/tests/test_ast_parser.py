from app.services.ast_parser import parse_file

SOURCE = '''
from src.auth import validate_token

def get_user(user_id, token):
    validate_token(token)
    return {"id": user_id}
'''


def test_parse_file_nodes():
    nodes, _edges = parse_file("src/users.py", SOURCE)
    names = {n.name for n in nodes}
    assert "get_user" in names
    assert any(n.id == "src.users.get_user" for n in nodes)


def test_parse_file_edges():
    _nodes, edges = parse_file("src/users.py", SOURCE)
    pairs = {(e.source, e.target) for e in edges}
    assert ("src.users.get_user", "src.auth.validate_token") in pairs
