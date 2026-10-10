from avp.providers.claude import strict_schema
from avp.stages.writing import SCRIPT_SCHEMA


def test_strict_schema_closes_every_object_without_mutating_input():
    strict = strict_schema(SCRIPT_SCHEMA)
    assert "additionalProperties" not in SCRIPT_SCHEMA

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from objects(v)
        elif isinstance(node, list):
            for v in node:
                yield from objects(v)

    found = list(objects(strict))
    assert len(found) > 5 and all(o["additionalProperties"] is False for o in found)
