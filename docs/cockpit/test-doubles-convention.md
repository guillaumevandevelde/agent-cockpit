---
title: "Test-doubles convention — patch where the consumer looks"
type: reference
status: active
---

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

## Singleton per-id state — onderdeel van het test-isolation contract

De conftest (``backend/tests/conftest.py``) doet per-test `drop_all` + `create_all`
op `app.database.Base` (zie kanban-kaart 02e80e79). Daardoor resetten
auto-increment id's naar 1 *iedere test*. Module-level singletons in
`app/services/*.py` houden regelmatig per-id in-memory state op de instance
bv. — op 2026-08-15 heeft géén singleton per-id in-memory state meer
(de `_last_auto_nudge_at` ging weg met de wake-lus, kaart `64b259f6…`):

| Singleton                              | Veld                     | Key-type          |

Als die state niet tussen tests wordt geleegd, lekt een eerdere test z'n
cooldown/rate-limit counters het volgende test_id=1 record in — en de
volgende test faalt met een *spurious* "rate limit exceeded" of "cooldown
not expired" error die in isolatie niet reproduceert.

**Voeg je singleton toe** aan dezelfde centrale reset-helper zodra
er weer per-id in-memory state bij komt — niet via een per-file autouse
fixture, want die vergeet de volgende persoon die een conftest-reset
aanpast.

Audit-recept (self-improve kaart 42f44a05):

```bash
# 1. Vind module-level singletons.
grep -rEn '^[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Z][A-Za-z]+\(\)' backend/app/services/

# 2. Per singleton: check of de __init__ een dict[int, ...] / deque / set aanmaakt
#    OF dat methodes die schrijven naar self._<iets>[<row_id>] bestaan.
# 3. Voeg toe aan de centrale reset-helper (de naam kan veranderen — zoek in
#    `backend/tests/conftest.py` naar de `_reset_*` autouse fixtures) als de
#    key een row-id is (geen string-keys zoals session_name / cwd / path).
```
