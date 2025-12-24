from __future__ import annotations

import ast
import os
import sqlite3
from dataclasses import dataclass


@dataclass
class Symbol:
    qname: str
    kind: str  # function|class|method
    lineno: int
    end_lineno: int
    col: int

def extract_symbols(source: str) -> list[Symbol]:
    tree = ast.parse(source)
    syms: list[Symbol] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            syms.append(Symbol(node.name, "function", node.lineno, node.end_lineno or node.lineno, node.col_offset))
        elif isinstance(node, ast.ClassDef):
            syms.append(Symbol(node.name, "class", node.lineno, node.end_lineno or node.lineno, node.col_offset))
            # methods
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef):
                    qn = f"{node.name}.{sub.name}"
                    syms.append(Symbol(qn, "method", sub.lineno, sub.end_lineno or sub.lineno, sub.col_offset))
    return syms

def ensure_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS symbols(
            path TEXT NOT NULL,
            qname TEXT NOT NULL,
            kind TEXT NOT NULL,
            lineno INTEGER NOT NULL,
            end_lineno INTEGER NOT NULL,
            col INTEGER NOT NULL,
            PRIMARY KEY(path, qname, kind, lineno)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path)")
        con.commit()
    finally:
        con.close()

def index_file(db_path: str, rel_path: str, source: str) -> list[Symbol]:
    ensure_db(db_path)
    syms = extract_symbols(source)
    con = sqlite3.connect(db_path)
    try:
        con.execute("DELETE FROM symbols WHERE path = ?", (rel_path,))
        con.executemany(
            "INSERT INTO symbols(path,qname,kind,lineno,end_lineno,col) VALUES(?,?,?,?,?,?)",
            [(rel_path, s.qname, s.kind, s.lineno, s.end_lineno, s.col) for s in syms]
        )
        con.commit()
    finally:
        con.close()
    return syms
