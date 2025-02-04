#!/usr/bin/env python3

import logging
import pathlib
import re
import subprocess
import typing
import itertools

import inflection

from swift.codegen.lib import schema, ql

log = logging.getLogger(__name__)


class Error(Exception):
    def __str__(self):
        return self.args[0]


class FormatError(Error):
    pass


class ModifiedStubMarkedAsGeneratedError(Error):
    pass


def get_ql_property(cls: schema.Class, prop: schema.Property):
    common_args = dict(
        type=prop.type if not prop.is_predicate else "predicate",
        qltest_skip="qltest_skip" in prop.pragmas,
        is_child=prop.is_child,
        is_optional=prop.is_optional,
        is_predicate=prop.is_predicate,
    )
    if prop.is_single:
        return ql.Property(
            **common_args,
            singular=inflection.camelize(prop.name),
            tablename=inflection.tableize(cls.name),
            tableparams=[
                "this"] + ["result" if p is prop else "_" for p in cls.properties if p.is_single],
        )
    elif prop.is_repeated:
        return ql.Property(
            **common_args,
            singular=inflection.singularize(inflection.camelize(prop.name)),
            plural=inflection.pluralize(inflection.camelize(prop.name)),
            tablename=inflection.tableize(f"{cls.name}_{prop.name}"),
            tableparams=["this", "index", "result"],
        )
    elif prop.is_optional:
        return ql.Property(
            **common_args,
            singular=inflection.camelize(prop.name),
            tablename=inflection.tableize(f"{cls.name}_{prop.name}"),
            tableparams=["this", "result"],
        )
    elif prop.is_predicate:
        return ql.Property(
            **common_args,
            singular=inflection.camelize(
                prop.name, uppercase_first_letter=False),
            tablename=inflection.underscore(f"{cls.name}_{prop.name}"),
            tableparams=["this"],
        )


def get_ql_class(cls: schema.Class):
    pragmas = {k: True for k in cls.pragmas if k.startswith("ql")}
    return ql.Class(
        name=cls.name,
        bases=cls.bases,
        final=not cls.derived,
        properties=[get_ql_property(cls, p) for p in cls.properties],
        dir=cls.dir,
        **pragmas,
    )


def get_import(file: pathlib.Path, swift_dir: pathlib.Path):
    stem = file.relative_to(swift_dir / "ql/lib").with_suffix("")
    return str(stem).replace("/", ".")


def get_types_used_by(cls: ql.Class):
    for b in cls.bases:
        yield b
    for p in cls.properties:
        yield p.type


def get_classes_used_by(cls: ql.Class):
    return sorted(set(t for t in get_types_used_by(cls) if t[0].isupper()))


_generated_stub_re = re.compile(r"private import .*\n\nclass \w+ extends \w+ \{[ \n]\}", re.MULTILINE)


def _is_generated_stub(file):
    with open(file) as contents:
        for line in contents:
            if not line.startswith("// generated"):
                return False
            break
        else:
            # no lines
            return False
        # one line already read, if we can read 5 other we are past the normal stub generation
        line_threshold = 5
        first_lines = list(itertools.islice(contents, line_threshold))
        if len(first_lines) == line_threshold or not _generated_stub_re.match("".join(first_lines)):
            raise ModifiedStubMarkedAsGeneratedError(
                f"{file.name} stub was modified but is still marked as generated")
        return True


def format(codeql, files):
    format_cmd = [codeql, "query", "format", "--in-place", "--"]
    format_cmd.extend(str(f) for f in files if f.suffix in (".qll", ".ql"))
    res = subprocess.run(format_cmd, stderr=subprocess.PIPE, text=True)
    if res.returncode:
        for line in res.stderr.splitlines():
            log.error(line.strip())
        raise FormatError("QL format failed")
    for line in res.stderr.splitlines():
        log.debug(line.strip())


def _get_all_properties(cls: ql.Class, lookup: typing.Dict[str, ql.Class]) -> typing.Iterable[
        typing.Tuple[ql.Class, ql.Property]]:
    for b in cls.bases:
        base = lookup[b]
        for item in _get_all_properties(base, lookup):
            yield item
    for p in cls.properties:
        yield cls, p


def _get_all_properties_to_be_tested(cls: ql.Class, lookup: typing.Dict[str, ql.Class]) -> typing.Iterable[
        ql.PropertyForTest]:
    # deduplicate using id
    already_seen = set()
    for c, p in _get_all_properties(cls, lookup):
        if not (c.qltest_skip or p.qltest_skip or id(p) in already_seen):
            already_seen.add(id(p))
            yield ql.PropertyForTest(p.getter, p.type, p.is_single, p.is_predicate, p.is_repeated)


def _partition(l, pred):
    """ partitions a list according to boolean predicate """
    res = ([], [])
    for x in l:
        res[not pred(x)].append(x)
    return res


def _is_in_qltest_collapsed_hierachy(cls: ql.Class, lookup: typing.Dict[str, ql.Class]):
    return cls.qltest_collapse_hierarchy or _is_under_qltest_collapsed_hierachy(cls, lookup)


def _is_under_qltest_collapsed_hierachy(cls: ql.Class, lookup: typing.Dict[str, ql.Class]):
    return not cls.qltest_uncollapse_hierarchy and any(
        _is_in_qltest_collapsed_hierachy(lookup[b], lookup) for b in cls.bases)


def _should_skip_qltest(cls: ql.Class, lookup: typing.Dict[str, ql.Class]):
    return cls.qltest_skip or not (cls.final or cls.qltest_collapse_hierarchy) or _is_under_qltest_collapsed_hierachy(
        cls, lookup)


def generate(opts, renderer):
    input = opts.schema
    out = opts.ql_output
    stub_out = opts.ql_stub_output
    test_out = opts.ql_test_output
    missing_test_source_filename = "MISSING_SOURCE.txt"

    existing = {q for q in out.rglob("*.qll")}
    existing |= {q for q in stub_out.rglob("*.qll") if _is_generated_stub(q)}
    existing |= {q for q in test_out.rglob("*.ql")}
    existing |= {q for q in test_out.rglob(missing_test_source_filename)}

    data = schema.load(input)

    classes = [get_ql_class(cls) for cls in data.classes]
    lookup = {cls.name: cls for cls in classes}
    classes.sort(key=lambda cls: (cls.dir, cls.name))
    imports = {}

    for c in classes:
        imports[c.name] = get_import(stub_out / c.path, opts.swift_dir)

    for c in classes:
        qll = out / c.path.with_suffix(".qll")
        c.imports = [imports[t] for t in get_classes_used_by(c)]
        renderer.render(c, qll)
        stub_file = stub_out / c.path.with_suffix(".qll")
        if not stub_file.is_file() or _is_generated_stub(stub_file):
            stub = ql.Stub(
                name=c.name, base_import=get_import(qll, opts.swift_dir))
            renderer.render(stub, stub_file)

    # for example path/to/elements -> path/to/elements.qll
    include_file = stub_out.with_suffix(".qll")
    renderer.render(ql.ImportList(list(imports.values())), include_file)

    renderer.render(ql.GetParentImplementation(
        classes), out / 'GetImmediateParent.qll')

    for c in classes:
        if _should_skip_qltest(c, lookup):
            continue
        test_dir = test_out / c.path
        test_dir.mkdir(parents=True, exist_ok=True)
        if not any(test_dir.glob("*.swift")):
            log.warning(f"no test source in {c.path}")
            renderer.render(ql.MissingTestInstructions(),
                            test_dir / missing_test_source_filename)
            continue
        total_props, partial_props = _partition(_get_all_properties_to_be_tested(c, lookup),
                                                lambda p: p.is_single or p.is_predicate)
        renderer.render(ql.ClassTester(class_name=c.name,
                                       properties=total_props), test_dir / f"{c.name}.ql")
        for p in partial_props:
            renderer.render(ql.PropertyTester(class_name=c.name,
                                              property=p), test_dir / f"{c.name}_{p.getter}.ql")

    renderer.cleanup(existing)
    if opts.ql_format:
        format(opts.codeql_binary, renderer.written)
