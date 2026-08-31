"""Syntax-check a JS file by compiling it without running it.

Wrapping the source in a function expression that is never called means the whole
body is parsed and compiled -- every syntax error surfaces -- while nothing
executes, so the absence of a DOM does not matter.
"""
import sys, quickjs
path = sys.argv[1]
src = open(path).read()
try:
    quickjs.Context().eval("(function(){\n" + src + "\n})")
except Exception as e:
    print(f"SYNTAX ERROR in {path}:\n  {e}")
    sys.exit(1)
print(f"{path}: compiles clean ({len(src.splitlines())} lines)")
