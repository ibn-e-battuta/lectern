using System.Globalization;
using System.Text.Json;

namespace Lectern.Services;

/// <summary>Forgiving reads from bitdocs JSON, where any field may be missing, null or of another type.</summary>
public static class Js
{
    public static JsonElement? Get(JsonElement? e, string key) =>
        e is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty(key, out var v) && v.ValueKind != JsonValueKind.Null ? v : null;

    public static bool Has(JsonElement? e, string key) =>
        e is { ValueKind: JsonValueKind.Object } o && o.TryGetProperty(key, out _);

    public static string? Str(JsonElement? e, string key) =>
        Get(e, key) is { ValueKind: JsonValueKind.String } s ? s.GetString() : null;

    /// <summary>Children of an array; nothing when it isn't one.</summary>
    public static List<JsonElement> Items(JsonElement? e) =>
        e is { ValueKind: JsonValueKind.Array } a ? a.EnumerateArray().ToList() : [];

    /// <summary>False for null, "", 0, false and empty arrays or objects.</summary>
    public static bool Truthy(JsonElement? e) => e is { } v && v.ValueKind switch
    {
        JsonValueKind.String => v.GetString()!.Length > 0,
        JsonValueKind.Number => v.GetDouble() != 0,
        JsonValueKind.True => true,
        JsonValueKind.Array => v.GetArrayLength() > 0,
        JsonValueKind.Object => v.EnumerateObject().Any(),
        _ => false,
    };

    /// <summary>The first of these fields that has a value.</summary>
    public static JsonElement? First(JsonElement? e, params string[] keys)
    {
        foreach (var k in keys)
            if (Get(e, k) is { } v && Truthy(v)) return v;
        return null;
    }

    /// <summary>A value as text: strings as they are, numbers and booleans spelled out.</summary>
    public static string? PyStr(JsonElement? e) => e switch
    {
        null => null,
        { ValueKind: JsonValueKind.String } s => s.GetString(),
        { ValueKind: JsonValueKind.True } => "True",
        { ValueKind: JsonValueKind.False } => "False",
        { ValueKind: JsonValueKind.Number } n => n.GetRawText().IndexOfAny(['.', 'e', 'E']) < 0
            ? n.GetRawText()
            : n.GetDouble().ToString("R", CultureInfo.InvariantCulture),
        { ValueKind: JsonValueKind.Null } => null,
        { } other => other.GetRawText(),
    };

    public static int PyInt(JsonElement? e, int fallback)
    {
        if (e is not { } v) return fallback;
        if (v.ValueKind == JsonValueKind.Number) return v.TryGetInt32(out var i) ? i : (int)v.GetDouble();
        if (v.ValueKind == JsonValueKind.String && int.TryParse(v.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var s)) return s;
        return fallback;
    }
}
