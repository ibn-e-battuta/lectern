using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Lectern.Services;

/// <summary>
/// Small helpers that reproduce what the document folders already contain: page hashes, file names and
/// link encoding. Keeping them identical means folders synced earlier are recognised and not rewritten.
/// </summary>
public static partial class PyCompat
{
    /// <summary>sha256 of the JSON with sorted keys, ", "/": " separators and \u escapes for non-ASCII.</summary>
    public static string Hash(JsonElement e) => Sha256(Dumps(e));

    public static string Sha256(string text) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(text)));

    public static string Dumps(JsonElement e)
    {
        var sb = new StringBuilder();
        Write(sb, e);
        return sb.ToString();
    }

    /// <summary>Same form for a plain string → string map (the page link map).</summary>
    public static string Dumps(IReadOnlyDictionary<string, string> map)
    {
        var sb = new StringBuilder("{");
        var first = true;
        foreach (var (k, v) in map.OrderBy(p => p.Key, StringComparer.Ordinal))
        {
            if (!first) sb.Append(", ");
            first = false;
            WriteString(sb, k);
            sb.Append(": ");
            WriteString(sb, v);
        }
        return sb.Append('}').ToString();
    }

    private static void Write(StringBuilder sb, JsonElement e)
    {
        switch (e.ValueKind)
        {
            case JsonValueKind.Object:
                sb.Append('{');
                var first = true;
                foreach (var p in e.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    if (!first) sb.Append(", ");
                    first = false;
                    WriteString(sb, p.Name);
                    sb.Append(": ");
                    Write(sb, p.Value);
                }
                sb.Append('}');
                break;
            case JsonValueKind.Array:
                sb.Append('[');
                var i = 0;
                foreach (var x in e.EnumerateArray())
                {
                    if (i++ > 0) sb.Append(", ");
                    Write(sb, x);
                }
                sb.Append(']');
                break;
            case JsonValueKind.String: WriteString(sb, e.GetString()!); break;
            case JsonValueKind.Number: sb.Append(Number(e.GetRawText())); break;
            case JsonValueKind.True: sb.Append("true"); break;
            case JsonValueKind.False: sb.Append("false"); break;
            default: sb.Append("null"); break;
        }
    }

    private static void WriteString(StringBuilder sb, string s)
    {
        sb.Append('"');
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (c < 0x20 || c > 0x7f) sb.Append("\\u").Append(((int)c).ToString("x4"));
                    else sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }

    /// <summary>Integers keep their digits; other numbers are written the way Python prints a float.</summary>
    private static string Number(string raw)
    {
        if (raw.IndexOfAny(['.', 'e', 'E']) < 0) return raw.TrimStart('+');
        var d = double.Parse(raw, CultureInfo.InvariantCulture);
        if (double.IsInfinity(d)) return d > 0 ? "Infinity" : "-Infinity";
        // Shortest round-trip digits, then Python's repr layout: fixed for 1e-4 <= |x| < 1e16, else scientific.
        var shortest = d.ToString("R", CultureInfo.InvariantCulture);
        var neg = shortest.StartsWith('-');
        if (neg) shortest = shortest[1..];
        var ePos = shortest.IndexOfAny(['E', 'e']);
        var mant = ePos >= 0 ? shortest[..ePos] : shortest;
        var expPart = ePos >= 0 ? int.Parse(shortest[(ePos + 1)..], CultureInfo.InvariantCulture) : 0;
        var dot = mant.IndexOf('.');
        var intPart = dot >= 0 ? mant[..dot] : mant;
        var all = intPart + (dot >= 0 ? mant[(dot + 1)..] : "");
        var digits = all.TrimStart('0');
        if (digits.Length == 0) return neg ? "-0.0" : "0.0";
        var exp = intPart.Length - (all.Length - digits.Length) - 1 + expPart; // exponent of the first significant digit
        digits = digits.TrimEnd('0');
        string body;
        if (exp < -4 || exp >= 16) body = Sci(digits, exp);
        else if (exp >= 0)
        {
            var ip = digits.Length > exp + 1 ? digits[..(exp + 1)] : digits.PadRight(exp + 1, '0');
            var fp = digits.Length > exp + 1 ? digits[(exp + 1)..] : "0";
            body = ip + "." + fp;
        }
        else body = "0." + new string('0', -exp - 1) + digits;
        return (neg ? "-" : "") + body;

        static string Sci(string digits, int exp)
        {
            var m = digits.Length > 1 ? digits[0] + "." + digits[1..] : digits;
            return m + "e" + (exp < 0 ? "-" : "+") + Math.Abs(exp).ToString("00", CultureInfo.InvariantCulture);
        }
    }

    // ---------- names and paths ----------

    [GeneratedRegex(@"[\\/:*?""<>|\x00-\x1f]")]
    private static partial Regex UnsafeChars();

    [GeneratedRegex(@"\s+")]
    private static partial Regex Spaces();

    /// <summary>A file or folder name that works everywhere, at most <paramref name="maxLen"/> characters.</summary>
    public static string SafeName(string? s, int maxLen = 90)
    {
        s = UnsafeChars().Replace(string.IsNullOrEmpty(s) ? "Untitled" : s, "-");
        s = Spaces().Replace(s, " ").Trim(' ', '.');
        if (s.Length > maxLen) s = s[..maxLen].TrimEnd(' ', '.', '-');
        return s.Length == 0 ? "Untitled" : s;
    }

    /// <summary>Splits off the extension like Python's os.path.splitext (".bashrc" has none).</summary>
    public static (string Stem, string Ext) SplitExt(string path)
    {
        var slash = path.LastIndexOf('/');
        var dot = path.LastIndexOf('.');
        if (dot > slash)
            // Leading dots of the name don't start an extension.
            for (var i = slash + 1; i < dot; i++)
                if (path[i] != '.') return (path[..dot], path[dot..]);
        return (path, "");
    }

    private const string AlwaysSafe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~";

    /// <summary>Percent-encodes UTF-8 like urllib.parse.quote with the given extra safe characters.</summary>
    public static string Quote(string s, string safe = "/")
    {
        var sb = new StringBuilder();
        foreach (var b in Encoding.UTF8.GetBytes(s))
        {
            var c = (char)b;
            if (b < 0x80 && (AlwaysSafe.Contains(c) || safe.Contains(c))) sb.Append(c);
            else sb.Append('%').Append(b.ToString("X2"));
        }
        return sb.ToString();
    }

    /// <summary>Relative path with forward slashes (all paths in the state file use them).</summary>
    public static string RelPath(string path, string relativeTo) =>
        Path.GetRelativePath(relativeTo, path).Replace('\\', '/');

    /// <summary>The path part of a URL, as written (not decoded).</summary>
    public static string UrlPath(string url)
    {
        var s = url;
        var scheme = s.IndexOf("://", StringComparison.Ordinal);
        if (scheme >= 0)
        {
            s = s[(scheme + 3)..];
            var slash = s.IndexOf('/');
            s = slash >= 0 ? s[slash..] : "";
        }
        var cut = s.IndexOfAny(['?', '#']);
        if (cut >= 0) s = s[..cut];
        var semi = s.IndexOf(';');
        return semi >= 0 ? s[..semi] : s;
    }
}
