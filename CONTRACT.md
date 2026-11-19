# Event validation contract

`settlelite.parse_cents(value)` accepts a Python integer, including negative and
arbitrarily large values, without rounding or conversion. It rejects booleans,
floats, strings, and null with ValueError. Individual event rules constrain signs.

`settlelite.decode_event(payload)` accepts a JSON object string or a mapping and
returns a frozen `settlelite.Event`. Invalid input raises `settlelite.EventError`,
a ValueError. Each envelope has nonempty string `version`, `request_id`, and
`item_id`, and `kind` equal to `post` or `void`. Whitespace is significant inside
identifiers; object member order and JSON whitespace are not identity.

A post requires nonnegative integer `amount_cents`. Its optional nonnegative
integer `revision` defaults to zero. A void has neither amount_cents nor revision.
Unknown members are invalid. Event validation accepts every syntactically valid
nonempty version string; admission to a ledger is a separate feature.

`settlelite.encode_event(event)` returns compact, sorted JSON for the normalized
envelope. Posts include the defaulted revision; voids omit amount and revision.
Decoding this representation preserves the Event exactly.

`python -m settlelite validate` reads one JSON envelope per stdin line. A valid
line returns `{"ok":true,"event":<normalized object>}`; an invalid line returns
`{"ok":false,"error":"invalid_event","detail":<diagnostic string>}`. Every
result is a compact JSON object followed by one newline. Processing continues
after invalid lines. Exit is zero if every line is valid, two otherwise; empty
input is a successful stream with no results. The diagnostic wording is not
contractual. The CLI never mutates a ledger or creates a database.
