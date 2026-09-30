"""seq_api static route tests (FastAPI is not importable in the sandbox):
py_compile + ast checks against the contract §4 table and §8.2 rules."""
import ast
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = ROOT / "seq_api.py"

# (METHOD, path) -> (function name, service fn or None)
TABLE = {
    ("GET", "/api/seq/whoami"): ("r_whoami", None),
    ("GET", "/api/seq/grades"): ("r_grades", "grades"),
    ("GET", "/api/seq/slice"): ("r_get_slice", "get_slice"),
    ("GET", "/api/seq/node/{source_key}"): ("r_get_node", "get_node"),
    ("GET", "/api/seq/compare"): ("r_get_compare", "get_compare"),
    ("GET", "/api/seq/sequence"): ("r_get_sequence", "get_sequence"),
    ("GET", "/api/seq/guardrail"): ("r_get_guardrail", "get_guardrail"),
    ("GET", "/api/seq/attention"): ("r_get_attention", "get_attention"),
    ("GET", "/api/seq/sequences/{sequence_id}/events"): ("r_get_events", "get_events"),
    ("POST", "/api/seq/sequences"): ("r_create_sequence", "create_sequence"),
    ("PATCH", "/api/seq/sequences/{sequence_id}"): ("r_update_sequence", "update_sequence"),
    ("POST", "/api/seq/sequences/{sequence_id}/modules"): ("r_create_module", "create_module"),
    ("PATCH", "/api/seq/modules/{module_id}"): ("r_update_module", "update_module"),
    ("POST", "/api/seq/modules/{module_id}/move"): ("r_move_module", "move_module"),
    ("DELETE", "/api/seq/modules/{module_id}"): ("r_remove_module", "remove_module"),
    ("PATCH", "/api/seq/slots/{slot_id}"): ("r_update_slot", "update_slot"),
    ("POST", "/api/seq/slots/{slot_id}/move"): ("r_move_slot", "move_slot"),
    ("POST", "/api/seq/slots/{slot_id}/merge"): ("r_merge_slot", "merge_slot"),
    ("POST", "/api/seq/slots"): ("r_create_slot_group", "create_slot_group"),
    ("POST", "/api/seq/placements"): ("r_place", "place"),
    ("PATCH", "/api/seq/placements/{placement_id}"): ("r_update_placement", "update_placement"),
    ("POST", "/api/seq/placements/{placement_id}/co-place"): ("r_co_place", "co_place"),
    ("POST", "/api/seq/placements/{placement_id}/ungroup"): ("r_ungroup", "ungroup"),
    ("DELETE", "/api/seq/placements/{placement_id}"): ("r_remove_placement", "remove_placement"),
    ("POST", "/api/seq/placements/{placement_id}/reattach"): ("r_reattach", "reattach"),
    ("POST", "/api/seq/placements/{placement_id}/acknowledge"): ("r_acknowledge", "acknowledge"),
    ("GET", "/api/seq/views"): ("r_list_views", "list_views"),
    ("POST", "/api/seq/views"): ("r_create_view", "create_view"),
    ("PUT", "/api/seq/views/{view_id}"): ("r_update_view", "update_view"),
    ("DELETE", "/api/seq/views/{view_id}"): ("r_delete_view", "delete_view"),
    ("GET", "/seq"): ("r_page_redirect", None),
    ("GET", "/seq/"): ("r_page", None),
    ("GET", "/seq/assets/{name}"): ("r_asset", None),
}


def _tree():
    return ast.parse(SRC_PATH.read_text())


def _routes():
    out = {}
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"
                    and dec.func.attr in ("get", "post", "patch", "put", "delete")):
                path = dec.args[0].value
                out[(dec.func.attr.upper(), path)] = node
    return out


def test_compiles():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        py_compile.compile(str(SRC_PATH), cfile=str(Path(d) / "seq_api.pyc"), doraise=True)


def test_route_set_matches_contract_table():
    got = set(_routes())
    assert got == set(TABLE), (sorted(got - set(TABLE)), sorted(set(TABLE) - got))
    assert len(got) == 33


def test_route_bodies_are_single_run_calls():
    for key, node in _routes().items():
        name, fn = TABLE[key]
        assert node.name == name, (key, node.name)
        if fn is None:
            continue
        assert len(node.body) == 1, f"{name}: body is not one statement"
        stmt = node.body[0]
        assert isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Call), name
        call = stmt.value
        assert isinstance(call.func, ast.Name) and call.func.id == "_run", name
        a0, a1 = call.args[0], call.args[1]
        assert (isinstance(a0, ast.Attribute) and isinstance(a0.value, ast.Name)
                and a0.value.id == "seq_service" and a0.attr == fn), (name, ast.dump(a0))
        assert isinstance(a1, ast.Name) and a1.id == "CTX", name


def _default_is_depends_current_writer(node, pname):
    args = node.args
    allargs = args.args
    defaults = [None] * (len(allargs) - len(args.defaults)) + list(args.defaults)
    for a, d in zip(allargs, defaults):
        if a.arg == pname:
            return (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    and d.func.id == "Depends" and isinstance(d.args[0], ast.Name)
                    and d.args[0].id == "current_writer")
    return False


def test_write_and_view_routes_take_writer_dependency():
    n = 0
    for (method, path), node in _routes().items():
        if path.startswith("/api/seq/views"):
            assert _default_is_depends_current_writer(node, "owner"), node.name
            n += 1
        elif method in ("POST", "PATCH", "PUT", "DELETE"):
            assert _default_is_depends_current_writer(node, "writer"), node.name
            n += 1
    assert n == 4 + 17, n   # V1-V4 plus W1-W17


def test_forbidden_strings_absent():
    src = SRC_PATH.read_text()
    for bad in ("sqlite3", "execute(", "seq_store", "seq_read", "app.mount", "StaticFiles"):
        assert bad not in src, bad


def test_single_app_with_auth_gate():
    src = SRC_PATH.read_text()
    assert src.count("FastAPI(") == 1
    assert re.search(r"FastAPI\([^)]*dependencies=\[Depends\(require_writer\)\]", src)
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "APIRouter":
            assert not node.args and not node.keywords, "router must have no prefix"


def test_pydantic_models_ignore_extra_and_have_no_by_at_fields():
    tree = _tree()
    models = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    base = models["_Body"]
    assert "ConfigDict(extra=\"ignore\")" in ast.unparse(base).replace("'", '"')
    n_models = 0
    for name, cls in models.items():
        if name == "_Body":
            continue
        assert [b.id for b in cls.bases] == ["_Body"], name
        n_models += 1
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign):
                fname = stmt.target.id
                assert not (fname.endswith("_by") or fname.endswith("_at")), (name, fname)
    assert n_models >= 15


def test_static_whitelist_exact():
    for node in _tree().body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ASSET_WHITELIST":
            assert {e.value for e in node.value.elts} == {"app.js", "app.css"}
            return
    raise AssertionError("ASSET_WHITELIST not found")


def test_no_default_writer_in_bodies():
    # client cannot name the writer: no body model has placed_by/owner-by style fields
    src = SRC_PATH.read_text()
    assert "placed_by" in src  # documented in the comment only
    for node in ast.walk(_tree()):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assert node.target.id != "placed_by"


def _main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, repr(e))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
