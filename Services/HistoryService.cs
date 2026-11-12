using System.Text;
using LibGit2Sharp;

namespace Lectern.Services;

public sealed record Snapshot(string Sha, DateTimeOffset When, string Message);

public sealed record SnapshotFile(string Path, string Status, int Added, int Deleted);

public sealed record SnapshotDetail(Snapshot Snapshot, List<SnapshotFile> Files, string Diff);

/// <summary>
/// A git snapshot of the document folders after each sync. The repository is kept in Lectern's config folder with the
/// courses folder as its working tree; an exclude file limits it to the document folders, so videos, your own projects
/// and their own git repositories are never tracked or touched.
/// </summary>
public sealed class HistoryService(LecternOptions options, ActivityLog log)
{
    private static readonly string[] Ignored =
        [".raw/", DocState.FileName, DocState.FileName + ".tmp", "*.part", "*.tmp", "*.pdf", ".DS_Store", "*.mp4", "*.mov", "*.m4v", "*.webm"];

    private readonly Lock _gate = new();

    private string GitDir => Path.Combine(options.ConfigPath, "history.git");

    public bool Exists => Directory.Exists(GitDir);

    private Repository Open()
    {
        if (!Repository.IsValid(GitDir))
        {
            Repository.Init(GitDir, isBare: true);
            using var init = new Repository(GitDir);
            init.Config.Set("core.bare", false);
            init.Config.Set("core.quotepath", false);
            init.Config.Set("core.precomposeunicode", true);
        }
        return new Repository(GitDir, new RepositoryOptions { WorkingDirectoryPath = options.CoursesPath });
    }

    private static Signature Sig() => new("Lectern", "lectern@localhost", DateTimeOffset.Now);

    /// <summary>Saves a snapshot if anything changed. Returns its short id.</summary>
    public string? Commit(string message, IEnumerable<string> docFolders)
    {
        lock (_gate)
        {
            using var repo = Open();
            WriteExcludes(docFolders);
            Commands.Stage(repo, "*");
            using var staged = repo.Diff.Compare<TreeChanges>(repo.Head.Tip?.Tree, DiffTargets.Index);
            if (staged.Count == 0) return null;
            var commit = repo.Commit(message, Sig(), Sig());
            var sha = commit.Sha[..7];
            log.Info($"History: saved snapshot {sha}");
            return sha;
        }
    }

    /// <summary>Ignore everything in the courses folder except the document folders.</summary>
    private void WriteExcludes(IEnumerable<string> folders)
    {
        var rules = new List<string> { "/*" };
        var allowed = new HashSet<string>();
        foreach (var f in folders.Order(StringComparer.Ordinal))
        {
            var rel = PyCompat.RelPath(f, options.CoursesPath);
            if (rel.StartsWith("..", StringComparison.Ordinal) || rel == ".") continue; // outside the courses folder: not tracked
            var parts = rel.Split('/');
            for (var i = 0; i < parts.Length; i++)
            {
                var prefix = "/" + string.Join("/", parts.Take(i + 1).Select(Escape));
                if (!allowed.Add(prefix)) continue;
                rules.Add($"!{prefix}/");
                if (i < parts.Length - 1) rules.Add($"{prefix}/*");
            }
        }
        rules.AddRange(Ignored);
        var info = Path.Combine(GitDir, "info");
        Directory.CreateDirectory(info);
        File.WriteAllText(Path.Combine(info, "exclude"), string.Join("\n", rules) + "\n");
    }

    /// <summary>Escapes a literal path segment for a gitignore pattern.</summary>
    private static string Escape(string part)
    {
        var sb = new StringBuilder();
        foreach (var ch in part)
        {
            if ("*?[]\\!#".Contains(ch)) sb.Append('\\');
            sb.Append(ch);
        }
        if (sb.Length > 0 && sb[^1] == ' ') sb.Insert(sb.Length - 1, '\\');
        return sb.ToString();
    }

    public List<Snapshot> List(int count = 60)
    {
        if (!Exists) return [];
        lock (_gate)
        {
            using var repo = Open();
            if (repo.Head.Tip is null) return [];
            return repo.Commits.Take(count).Select(c => new Snapshot(c.Sha, c.Author.When, c.MessageShort)).ToList();
        }
    }

    /// <summary>What changed in a snapshot: files with line counts, and the text diff of the Markdown pages.</summary>
    public SnapshotDetail? Show(string sha, int maxDiff = 400_000)
    {
        if (!Exists || sha.Length < 4 || !sha.All(Uri.IsHexDigit)) return null;
        lock (_gate)
        {
            using var repo = Open();
            if (repo.Lookup<Commit>(sha) is not { } commit) return null;
            var parent = commit.Parents.FirstOrDefault();
            var patch = repo.Diff.Compare<Patch>(parent?.Tree, commit.Tree);
            var files = patch.Select(p => new SnapshotFile(p.Path, p.Status.ToString(), p.LinesAdded, p.LinesDeleted)).ToList();
            var sb = new StringBuilder();
            foreach (var p in patch.Where(p => p.Path.EndsWith(".md", StringComparison.OrdinalIgnoreCase)))
            {
                sb.Append(p.Patch);
                if (sb.Length > maxDiff) { sb.Length = maxDiff; sb.Append("\n… (truncated)"); break; }
            }
            return new SnapshotDetail(new Snapshot(commit.Sha, commit.Author.When, commit.MessageShort), files, sb.ToString());
        }
    }

    /// <summary>Puts a file back as it was in a snapshot. The path is relative to the courses folder.</summary>
    public string? Restore(string sha, string path)
    {
        if (!Exists) return "There's no history yet.";
        lock (_gate)
        {
            using var repo = Open();
            if (repo.Lookup<Commit>(sha) is not { } commit) return "Unknown snapshot.";
            if (commit[path] is null) return "That file isn't in this snapshot.";
            repo.CheckoutPaths(commit.Sha, [path], new CheckoutOptions { CheckoutModifiers = CheckoutModifiers.Force });
            log.Info($"History: restored {path} from {commit.Sha[..7]}");
            return null;
        }
    }
}
