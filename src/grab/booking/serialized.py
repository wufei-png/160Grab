"""Read a bounded PHP serialized array, without object/reference deserialization."""


class ArrayReader:
    def __init__(self, raw):
        self.raw = raw.encode("utf-8")
        if len(self.raw) > 1_000_000:
            raise ValueError("Oversized array")
        self.pos = 0
        self.remaining = 10_000

    def expect(self, token):
        if not self.raw.startswith(token, self.pos):
            raise ValueError("Malformed array")
        self.pos += len(token)

    def until(self, delimiter):
        end = self.raw.index(delimiter, self.pos)
        value = self.raw[self.pos : end].decode("ascii")
        self.pos = end + len(delimiter)
        return value

    def read(self, depth=0):
        self.remaining -= 1
        if depth > 32 or self.remaining < 0:
            raise ValueError("Array budget exceeded")
        kind = self.raw[self.pos : self.pos + 1]
        self.pos += 1
        if kind == b"N":
            self.expect(b";")
            return None
        self.expect(b":")
        if kind == b"s":
            size = int(self.until(b":"))
            if size < 0:
                raise ValueError("Invalid string length")
            self.expect(b'"')
            value = self.raw[self.pos : self.pos + size].decode("utf-8")
            self.pos += size
            self.expect(b'";')
            return value
        if kind == b"a":
            size = int(self.until(b":"))
            if not 0 <= size <= 10_000:
                raise ValueError("Invalid array length")
            self.expect(b"{")
            pairs = [(self.read(depth + 1), self.read(depth + 1)) for _ in range(size)]
            self.expect(b"}")
            return pairs
        if kind in {b"i", b"b"}:
            return int(self.until(b";"))
        if kind == b"d":
            return float(self.until(b";"))
        raise ValueError("Unsupported serialized type")


def schedule_record_dates(raw, schedule_id):
    try:
        reader = ArrayReader(raw)
        root = reader.read()
        if reader.pos != len(reader.raw) or not isinstance(root, list):
            return []
        records = [v for k, v in root if str(k) == schedule_id]
        if len(records) != 1 or not isinstance(records[0], list):
            return []
        dates = []

        def visit(pairs):
            for key, value in pairs:
                if key == "to_date" and isinstance(value, str):
                    dates.append(value)
                elif isinstance(value, list):
                    visit(value)

        visit(records[0])
        return dates
    except (ValueError, TypeError, UnicodeError, IndexError):
        return []
