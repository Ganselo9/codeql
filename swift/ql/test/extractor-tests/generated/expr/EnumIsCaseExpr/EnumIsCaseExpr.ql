// generated by codegen/codegen.py
import codeql.swift.elements
import TestUtils

from EnumIsCaseExpr x, Expr getSubExpr, TypeRepr getTypeRepr, EnumElementDecl getElement
where
  toBeTested(x) and
  not x.isUnknown() and
  getSubExpr = x.getSubExpr() and
  getTypeRepr = x.getTypeRepr() and
  getElement = x.getElement()
select x, "getSubExpr:", getSubExpr, "getTypeRepr:", getTypeRepr, "getElement:", getElement
