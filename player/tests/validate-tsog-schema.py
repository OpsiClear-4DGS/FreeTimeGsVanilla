"""Check the published Draft 2020-12 schema against shared conformance cases."""
import copy
import json
from pathlib import Path
import zipfile

from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / "tsog.schema.json").read_text())
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema)
fixtures = root / "tests/fixtures/tsog"
corpus = json.loads((fixtures / "metadata-cases.json").read_text())
for case in corpus["cases"]:
    meta = copy.deepcopy(corpus["base"])
    for path, value in case["edits"]:
        keys = path.split(".")
        target = meta
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    errors = list(validator.iter_errors(meta))
    expected = case.get("schemaValid", case["valid"])
    assert (not errors) == expected, (case["name"], [error.message for error in errors])
for path in fixtures.glob("*.tsog"):
    with zipfile.ZipFile(path) as archive:
        validator.validate(json.loads(archive.read("meta.json")))
validator.validate(json.loads((fixtures / "decode-vector.json").read_text())["meta"])
print(f"Schema passed {len(corpus['cases'])} metadata cases, all synthetic archives and the decoding vector.")
