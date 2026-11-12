using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Lectern.Services;
using Xunit;

namespace Lectern.Tests;

/// <summary>
/// Compares the converter with output captured from earlier Lectern versions. The pages are real course
/// documents, so they are never committed: point LECTERN_GOLDEN at a folder with expected.json and the
/// page files to run this test.
/// </summary>
public class GoldenTests
{
    [Fact]
    public void Markdown_and_hashes_match_earlier_output()
    {
        var dir = Environment.GetEnvironmentVariable("LECTERN_GOLDEN");
        if (string.IsNullOrEmpty(dir) || !File.Exists(Path.Combine(dir, "expected.json"))) return;

        using var expected = JsonDocument.Parse(File.ReadAllText(Path.Combine(dir, "expected.json")));
        var failures = new List<string>();
        var count = 0;
        foreach (var page in expected.RootElement.EnumerateObject())
        {
            count++;
            using var raw = JsonDocument.Parse(File.ReadAllText(Path.Combine(dir, page.Name + ".json")));
            var body = Js.Get(raw.RootElement, "body_json");
            var conv = new MarkdownConverter(
                (url, video) => "assets/" + Convert.ToHexStringLower(SHA1.HashData(Encoding.UTF8.GetBytes(url)))[..8] + (video ? "-v" : ""),
                href => "L:" + href);
            var md = conv.Blocks(Js.Get(body, "content"));
            if (md != page.Value.GetProperty("md").GetString()) failures.Add($"markdown {page.Name}: {FirstDiff(md, page.Value.GetProperty("md").GetString()!)}");
            var hash = body is { } b ? PyCompat.Hash(b) : "";
            if (hash != page.Value.GetProperty("hash").GetString()) failures.Add($"hash {page.Name}");
        }
        Assert.True(count > 0, "expected.json lists no pages");
        Assert.True(failures.Count == 0, $"{failures.Count} of {count} pages differ:\n" + string.Join("\n", failures.Take(20)));
    }

    private static string FirstDiff(string a, string b)
    {
        var i = 0;
        while (i < a.Length && i < b.Length && a[i] == b[i]) i++;
        string Around(string s) => s.Substring(Math.Max(0, i - 40), Math.Min(100, s.Length - Math.Max(0, i - 40))).Replace("\n", "⏎");
        return $"at {i}: got «{Around(a)}» expected «{Around(b)}»";
    }
}
