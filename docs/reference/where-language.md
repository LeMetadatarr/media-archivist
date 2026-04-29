# WHERE Query Language Reference

Complete specification of the `--where` sandboxed expression language used in `list`, `urls`, and `export` subcommands.

## Overview

The `--where` flag accepts a sandboxed Python expression that filters `MediaEntry` objects. The evaluator is restricted to prevent code injection:

- **Allowed:** literals, identifiers, comparisons, boolean ops, arithmetic, function calls
- **Denied:** attribute access, imports, comprehensions, assignments, anything else

All identifiers refer to fields of the current `MediaEntry`. Missing fields evaluate to `None`.

## Grammar

```
expression  ::= boolean_expr
boolean_expr ::= or_expr
or_expr     ::= and_expr ( "or" and_expr )*
and_expr    ::= comparison ( "and" comparison )*
comparison  ::= arithmetic ( comp_op arithmetic )*
arithmetic  ::= term ( ("+" | "-") term )*
term        ::= factor ( ("*" | "/" | "%" | "//") factor )*
factor      ::= ("+" | "-" | "not") factor | atom
atom        ::= literal | identifier | call | "(" expression ")"

literal     ::= None | True | False | number | string
identifier  ::= name ( "[" name "]" )*    # only for dict access in raw; flat access preferred
name        ::= [a-zA-Z_][a-zA-Z0-9_]*
call        ::= name "(" [ expression ( "," expression )* ] ")"
comp_op     ::= "==" | "!=" | "<" | "<=" | ">" | ">=" | "in" | "not in"
```

## Literals

### Boolean

```python
# Use True, False (not lowercase)
explicit == True
```

### None

```python
# Test for missing/null values
artist == None
stream is not None  # NOT allowed; use != None instead
```

### Numbers

```python
# Integer or float
duration > 180
duration > 3.5
duration <= 600.0
```

### Strings

```python
# Single or double quotes (no escapes)
title == "My Title"
title == 'My Title'
source == "youtube"
```

## Identifiers

All entry field names are in scope:

| Field | Type | Notes |
|-------|------|-------|
| `id` | str | Stable entry ID |
| `source` | str | youtube, youtube_music, bandcamp, soundcloud, internet_archive |
| `url` | str | Media URL |
| `title` | str | Media title |
| `artist` | Optional[str] | Artist/creator name |
| `album` | Optional[str] | Album/collection name |
| `duration` | Optional[float] | Duration in seconds |
| `published` | Optional[str] | Publish date (ISO 8601) |
| `thumbnail` | Optional[str] | Thumbnail URL |
| `tags` | List[str] | Keywords/tags |
| `is_live` | bool | True if live stream |
| `explicit` | bool | True if flagged explicit |
| `stream` | Optional[str] | Direct playback stream URL |
| `canonical_id` | Optional[str] | Deduplication ID |
| `canonical_status` | Optional[str] | "matched", "quarantined", "unmatched" |

Missing fields evaluate to `None`.

### Examples

```python
title == "Foo"          # Exact match
source == "youtube"     # Source discriminator
duration > 180          # Duration longer than 3 minutes
artist == None          # Missing artist
explicit == False       # Non-explicit tracks
```

## Operators

### Arithmetic

Arithmetic operators are allowed on numeric values. Strings are not supported.

| Operator | Symbol | Example |
|----------|--------|---------|
| Addition | `+` | `duration + 60 < 3600` |
| Subtraction | `-` | `year - 2020 >= 0` |
| Multiplication | `*` | `duration * 0.016666 < 60` (seconds to minutes) |
| Division | `/` | `duration / 60 > 5` (minutes) |
| Floor division | `//` | `duration // 60 >= 3` |
| Modulo | `%` | `duration % 60` |

**Notes:**

- Operators follow standard Python precedence
- Division by zero raises an error
- Mixing None with arithmetic raises an error

### Comparison

All comparison operators are supported:

| Operator | Meaning | Example |
|----------|---------|---------|
| `==` | Equality | `source == "youtube"` |
| `!=` | Inequality | `artist != None` |
| `<` | Less than | `duration < 300` |
| `<=` | Less than or equal | `duration <= 3600` |
| `>` | Greater than | `duration > 180` |
| `>=` | Greater than or equal | `duration >= 60` |
| `in` | Membership in list/string | `"podcast" in tags` |
| `not in` | Non-membership | `"explicit" not in tags` |

**Special None Semantics:**

Ordering comparisons with `None` on either side fail closed (return `False`):

```python
duration < 300          # False if duration is None
duration >= 0           # False if duration is None (not >= anymore)
artist > "A"            # False if artist is None
```

Equality/inequality work normally:

```python
duration == None        # True if missing
artist != None          # True if present
```

### Boolean

Boolean operators combine conditions:

| Operator | Meaning | Example |
|----------|---------|---------|
| `and` | Logical AND | `duration > 180 and source == "youtube"` |
| `or` | Logical OR | `duration < 60 or duration > 3600` |
| `not` | Logical NOT | `not explicit` |

**Semantics:**

- `and` short-circuits: if left is False, right is not evaluated
- `or` short-circuits: if left is True, right is not evaluated
- `not` inverts truthiness (0, None, "", [], {} are falsy)

## Functions

Three built-in functions are allowed:

### len

```python
len(value) -> int
```

Returns the length of a sequence or string.

```python
len(title) > 10         # Title longer than 10 characters
len(tags) > 0           # At least one tag
len(tags) == 0          # No tags
len(album) < 50         # Album name shorter than 50 chars
```

### lower

```python
str.lower(text) -> str
```

Convert string to lowercase (for case-insensitive matching).

```python
lower(title) == "my title"         # Case-insensitive title match
lower(source) == "youtube_music"   # Redundant; source is already lowercase
```

### upper

```python
str.upper(text) -> str
```

Convert string to uppercase.

```python
upper(title) == "MY TITLE"
```

## Examples

### Basic Filters

```python
# All YouTube videos
source == "youtube"

# All music tracks
source == "youtube_music"

# Bandcamp tracks
source == "bandcamp"

# Internet Archive items
source == "internet_archive"

# Videos by Foo
artist == "Foo"

# Videos titled "Bar"
title == "Bar"

# Entries with a direct stream URL
stream != None

# Entries without a stream URL
stream == None

# Live streams
is_live == True

# Not live
is_live == False

# Explicit content
explicit == True

# Clean content
explicit == False
```

### Duration Filters

```python
# Longer than 3 minutes (180 seconds)
duration > 180

# Shorter than 10 minutes
duration < 600

# Between 5 and 10 minutes
duration > 300 and duration < 600

# At least 1 hour
duration >= 3600

# Shorter than 30 minutes
duration < 1800

# Video is at least 2 hours
duration >= 7200
```

### Combined Filters

```python
# YouTube videos longer than 3 minutes
source == "youtube" and duration > 180

# Music tracks by Foo that are explicit
source == "youtube_music" and artist == "Foo" and explicit == True

# Bandcamp entries with a stream URL and no explicit flag
source == "bandcamp" and stream != None and explicit == False

# Internet Archive items shorter than 1 hour
source == "internet_archive" and duration < 3600

# Non-live content with a title
is_live == False and len(title) > 0

# Any source except YouTube
source != "youtube"
```

### Text Matching

```python
# Case-insensitive title match
lower(title) == "my title"

# Title contains "podcast" (if in title field, not case-insensitive)
# Note: The language doesn't have substring operators; use if the title is exactly "podcast"
title == "podcast"

# Title longer than 20 characters
len(title) > 20

# Album name shorter than 50 characters
len(album) < 50

# Short titles
len(title) < 10
```

### Tag Filters

```python
# Entries with at least one tag
len(tags) > 0

# Entries with no tags
len(tags) == 0

# Entries with many tags (more than 5)
len(tags) > 5

# Entries with exactly one tag
len(tags) == 1

# If a specific tag is in the tags list
"podcast" in tags

# If a tag is NOT in the list
"explicit" not in tags
```

### Complex Expressions

```python
# YouTube or YouTube Music
source == "youtube" or source == "youtube_music"

# Music tracks longer than 3 minutes, not explicit
source == "youtube_music" and duration > 180 and explicit == False

# Anything with a title, published date, and stream URL
title != None and published != None and stream != None

# Live content OR very long videos (>2 hours)
is_live == True or duration > 7200

# Videos by artists whose name is longer than 5 characters
len(artist) > 5 and artist != None

# From any music source with a duration
(source == "youtube_music" or source == "bandcamp" or source == "soundcloud") and duration != None

# Non-explicit music with streams
(source == "youtube_music" or source == "bandcamp" or source == "soundcloud") and explicit == False and stream != None
```

### Arithmetic Examples

```python
# Duration in minutes (as arithmetic)
duration / 60 > 5           # Longer than 5 minutes

# Duration in minutes (floor division, integer result)
duration // 60 >= 3         # At least 3 full minutes

# Remainder: videos with a duration ending in 30+ seconds
duration % 60 >= 30

# Scaled duration
duration * 0.016666 < 30    # Approximately < 30 minutes (in seconds * conversion factor)
```

### Edge Cases and Gotchas

```python
# ✓ VALID: None handling
artist == None
artist != None
duration < 300  # False if duration is None (safe)

# ✗ INVALID: Attribute access (use flat field names)
entry.artist    # AttributeError → WhereError
entry['artist'] # AttributeError → WhereError

# ✓ VALID: Truthiness (for booleans and presence checks)
explicit        # True if explicit == True
not explicit    # True if explicit == False

# ✗ INVALID: Comprehensions, lambdas, imports
[x for x in tags]  # SyntaxError → WhereError
lambda x: x > 0     # SyntaxError → WhereError
import sys          # SyntaxError → WhereError

# ✓ VALID: Chained comparisons
180 < duration < 600    # Combined condition (all must be true)

# ✓ VALID: Parentheses for grouping
(source == "youtube" or source == "youtube_music") and duration > 180

# ✓ VALID: Unary operators
-duration           # Negation (unusual, but allowed)
+duration           # Unary plus (no-op)
not explicit        # Logical NOT
```

## Error Messages

### Syntax Errors

```
error: --where: invalid expression: <details>
```

Raised when the expression is not valid Python.

```python
# Bad: Missing closing paren
duration > 180 and source == "youtube"  # OK
duration > 180 and (source == "youtube"  # Error: mismatched paren
```

### Denied Syntax

```
error: --where: <problem>
```

Raised when the expression uses denied syntax (attribute access, imports, etc.).

```python
# Bad: Attribute access
entry.duration > 180  # Error: attribute access not allowed

# Bad: Method call
title.upper() == "FOO"  # Error: only len/lower/upper allowed as functions

# Bad: Dict subscript
raw["title"]  # Error: attribute access not allowed (even via subscript)

# Bad: List comprehension
[d for d in tags]  # Error: unsupported syntax
```

### Unknown Identifiers

```
error: --where: unknown name: foo
```

Raised when a name is not a field or allowed function.

```python
# Bad: Typo in field name
durationn > 180  # Error: unknown name

# Bad: Undefined variable
x > 5  # Error: unknown name
```

### Type Mismatches

```
unsupported operand type(s) for >: 'NoneType' and 'int'
```

Raised when operators are misused (though None comparisons are handled gracefully for ordering).

```python
# Bad: Divide by zero (may fail at eval time)
duration / 0 > 0  # Error: division by zero

# Bad: Mix incompatible types
"hello" > 5  # Error: unorderable types (if not None)
```

## Best Practices

1. **Use field names directly** (not via subscript/attribute access):
   ```python
   # Good
   duration > 180 and source == "youtube"
   
   # Bad
   raw["duration"] > 180  # Denied: attribute access
   ```

2. **Handle None explicitly** (for fields that can be missing):
   ```python
   # Good: Check before comparing
   artist != None and len(artist) > 5
   
   # Bad: Assumes artist is always present
   len(artist) > 5  # May fail if artist is None
   ```

3. **Use exact matching for strings** (no substring search without the language supporting it):
   ```python
   # Good
   title == "Exact Title"
   lower(title) == "exact title"
   
   # Bad (would need substring support)
   "podcast" in title  # 'in' only works on sequences/lists
   ```

4. **Group complex conditions with parentheses**:
   ```python
   # Good
   (source == "youtube" or source == "youtube_music") and duration > 180
   
   # May be ambiguous
   source == "youtube" or source == "youtube_music" and duration > 180
   ```

5. **Combine filters efficiently** (use `and` to narrow the result early):
   ```python
   # Good: Filter source first
   source == "youtube" and duration > 180
   
   # Less efficient (but still correct)
   duration > 180 and source == "youtube"
   ```

## Integration with CLI

```bash
# Single-quoted to prevent shell interpretation
media-archivist list --canonical --where 'duration > 180 and source == "youtube"'

# Double-quoted (escape inner quotes)
media-archivist list --canonical --where "duration > 180 and source == \"youtube\""

# Complex expression
media-archivist urls --canonical --where '(source == "youtube" or source == "youtube_music") and duration > 300 and explicit == False'
```
