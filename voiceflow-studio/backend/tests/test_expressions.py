import pytest

from app.services.expressions import ExpressionError, check_expression, evaluate, evaluate_value, parse_names

V = {"loan_amount": 600000, "city": "Mumbai", "age": "34", "name": "Asha",
     "dealer": {"city": "Pune", "name": "X"}, "items": [1, 2, 3], "dealer_found": True}


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("loan_amount > 500000 and city == 'Mumbai'", True),
        ("loan_amount > 500000 and city == 'Delhi'", False),
        ("city in ['mumbai', 'pune']", True),
        ("city not in ['Delhi']", True),
        ("not dealer_found", False),
        ("age >= 18", True),  # numeric string coercion
        ("loan_amount / 2 == 300000", True),
        ("loan_amount * 2 - 200000 + 1 == 1000001", True),
        ("len(name) == 4", True),
        ("lower(city) == 'mumbai'", True),
        ("city.lower() == 'mumbai'", True),
        ("dealer.city == 'Pune'", True),
        ("dealer['name'] == 'X'", True),
        ("items[0] == 1", True),
        ("1 < loan_amount < 10000000", True),
        ("true and not false", True),
        ("missing == None", True),
        ("missing is None", True),
        ("x if dealer_found else y", False),
    ],
)
def test_evaluate(expr, expected):
    assert evaluate(expr, V) is expected


def test_unknown_var_comparisons_false():
    assert evaluate("unknown > 5", {}) is False
    assert evaluate("unknown == 'a'", {}) is False
    assert evaluate("unknown != 'a'", {}) is False
    assert evaluate("unknown in ['a']", {}) is False
    assert evaluate("unknown", {}) is False


def test_runtime_type_error_is_false():
    assert evaluate("name > 5", V) is False
    assert evaluate("loan_amount / 0 > 1", V) is False
    assert evaluate_value("loan_amount / 0", V) is None


def test_parse_names():
    assert parse_names("loan_amount > 5 and lower(city) == 'x' or dealer.city == true") == {
        "loan_amount", "city", "dealer"}
    assert parse_names("len(items) > 0") == {"items"}


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "__import__('os').system('ls')",
        "open('/etc/passwd')",
        "eval('1')",
        "exec('x=1')",
        "().__class__.__bases__[0].__subclasses__()",
        "x.__class__",
        "_secret",
        "lambda: 1",
        "[x for x in items]",
        "x := 1",
        "2 ** 1000000",
        "getattr(x, 'y')",
        "len(x=1)",
        "items[0:2]",
        "name.replace('a', 'b')",
        "x = 1",
        "",
        "a ==",
        "a" * 2000,
    ],
)
def test_rejects_unsafe_or_invalid(expr):
    with pytest.raises(ExpressionError):
        evaluate(expr, V)
    assert check_expression(expr) is not None


def test_big_string_multiplication_blocked():
    assert evaluate("'a' * 100000000 == 'a'", {}) is False


def test_no_eval_used():
    import inspect

    import app.services.expressions as m

    src = inspect.getsource(m)
    assert "eval(" not in src.replace("_eval(", "").replace("evaluate(", "").replace("literal_eval", "")
    assert "exec(" not in src
