using DiffPlex;

namespace Lectern.Services;

/// <summary>Readable line diffs of a page's text for the change logs.</summary>
public static class TextDiff
{
    /// <summary>A unified diff with one line of context, without the "synced:" header line. Null when nothing changed.</summary>
    public static List<string>? Unified(string oldText, string newText, int maxLines = 80)
    {
        static string[] Lines(string t) => t.Replace("\r\n", "\n").Split('\n')
            .Where(ln => !ln.StartsWith("<!-- source:", StringComparison.Ordinal)).ToArray();
        var a = Lines(oldText);
        var b = Lines(newText);
        if (a.Length > 0 && a[^1].Length == 0) a = a[..^1];
        if (b.Length > 0 && b[^1].Length == 0) b = b[..^1];
        var result = Differ.Instance.CreateLineDiffs(string.Join("\n", a), string.Join("\n", b), ignoreWhitespace: false);
        if (result.DiffBlocks.Count == 0) return null;

        const int context = 1;
        var lines = new List<string>();
        var blocks = result.DiffBlocks;
        var i = 0;
        while (i < blocks.Count)
        {
            // Join blocks that are close enough to share their context lines.
            var j = i;
            while (j + 1 < blocks.Count && blocks[j + 1].DeleteStartA - (blocks[j].DeleteStartA + blocks[j].DeleteCountA) <= 2 * context) j++;
            var startA = Math.Max(0, blocks[i].DeleteStartA - context);
            var startB = Math.Max(0, blocks[i].InsertStartB - context);
            var endA = Math.Min(a.Length, blocks[j].DeleteStartA + blocks[j].DeleteCountA + context);
            var endB = Math.Min(b.Length, blocks[j].InsertStartB + blocks[j].InsertCountB + context);
            lines.Add($"@@ -{Range(startA, endA - startA)} +{Range(startB, endB - startB)} @@");
            var posA = startA;
            for (var k = i; k <= j; k++)
            {
                var blk = blocks[k];
                for (; posA < blk.DeleteStartA; posA++) lines.Add(" " + a[posA]);
                for (var x = 0; x < blk.DeleteCountA; x++) lines.Add("-" + a[blk.DeleteStartA + x]);
                for (var x = 0; x < blk.InsertCountB; x++) lines.Add("+" + b[blk.InsertStartB + x]);
                posA = blk.DeleteStartA + blk.DeleteCountA;
            }
            for (; posA < endA; posA++) lines.Add(" " + a[posA]);
            i = j + 1;
        }
        if (lines.Count < 6) lines = lines.Where(l => !l.StartsWith("@@", StringComparison.Ordinal)).ToList();
        if (lines.Count == 0) return null;
        if (lines.Count > maxLines) lines = [.. lines.Take(maxLines), $"… ({lines.Count - maxLines} more lines)"];
        return lines;

        static string Range(int start, int count) => count == 1 ? $"{start + 1}" : $"{(count == 0 ? start : start + 1)},{count}";
    }

    /// <summary>A collapsed "show what changed" block for Markdown change logs.</summary>
    public static IEnumerable<string> Block(List<string>? diff, string indent = "")
    {
        if (diff is null || diff.Count == 0) yield break;
        yield return indent + "<details><summary>show what changed</summary>";
        yield return "";
        yield return indent + "```diff";
        foreach (var ln in diff) yield return indent + ln.Replace("```", "ˋˋˋ");
        yield return indent + "```";
        yield return "";
        yield return indent + "</details>";
    }
}
