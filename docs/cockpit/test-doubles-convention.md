# Test-doubles convention — patch where the consumer looks

`from app.module import name` binds the function object into the consumer's namespace
**at import time**. A patch on the *source* module
(`monkeypatch.setattr(src_module, "name", patched)`) does **not** reach that binding —
the consumer keeps calling the original. The 11 dispatch tests in
`backend/tests/test_subscription_pool_dispatch.py` shipped green for that exact
reason (zie [subscription-pool-analyse §3](./subscription-pool-dispatch-analyse.md) /
kanban-kaart `ea7e038b…`).

## Three rules (must hold together)

1. **Patch the consumer.** `monkeypatch.setattr(consumer_mod, "name", ...)` — works
   regardless of how the consumer imports the symbol. This is the default.
2. **Or switch the consumer to module-attribute access.** `from app.module import
   consumer_mod` then `consumer_mod.name(...)`. Looks up the attribute on the module
   object at call time, so a patch on the source module IS visible. Use this when
   the consumer is genuinely a thin caller that you'd rather not patch directly.
3. **Always assert the double fired.** `patched.call_count == N`, explicit
   `calls == [...]`, or a sentinel the patched function mutates. Without that, a
   no-op patch is indistinguishable from a working one — the test passes green while
   injecting nothing.

## Reviewer grep-recept — twee scans

De verdachte combinatie is: test patcht *source*-module **+** consument doet
`from app.<src> import … <name>`. Twee scans lichten dat uit:

```bash
# 1. Tests die een source-module patchen (eerste twee args = module, naam).
#    Treffers zijn potentieel onzichtbaar als de consument `from X import name` doet.
grep -rnE 'monkeypatch\.setattr\(\s*[A-Za-z_][A-Za-z_0-9_.]*\s*,\s*"[A-Za-z_][A-Za-z_0-9]*"\s*,' \
    backend/tests/

# 2. Per gevonden `(src, name)` paar: check of een consument
#    `from app.<src> import … <name>` doet — die ziet de patch NIET.
#    Handmatig per paar:
#      grep -rnE '^from\s+app\.<src>\s+import\b.*\<<name>\>' backend/app/
```

Treffer op 1 + 2 = de patch is onzichtbaar voor die consument → óf de patch moet
naar `consumer_mod`, óf de consument moet module-attribute-access gebruiken
(zoals `dispatch.py` deed in de D5-fix).
