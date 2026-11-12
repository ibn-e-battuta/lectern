using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace Lectern.Data;

public sealed class AppUser
{
    public int Id { get; set; }
    public string Username { get; set; } = "";
    public string PasswordHash { get; set; } = "";
    public bool IsAdmin { get; set; }
    /// <summary>Changes when the password changes, which signs out every other session.</summary>
    public string SecurityStamp { get; set; } = Guid.NewGuid().ToString("N");
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime? LastLoginAt { get; set; }
}

/// <summary>A document to keep in sync, in the order it was added.</summary>
public sealed class Doc
{
    public int Id { get; set; }
    public string Host { get; set; } = "";
    public string Guid { get; set; } = "";
    /// <summary>Folder chosen for it, relative to the courses folder; null for a folder named after the title.</summary>
    public string? Folder { get; set; }
    public string? Title { get; set; }
    /// <summary>Where it was last written, relative to the courses folder.</summary>
    public string? OutputPath { get; set; }
    public DateTime AddedAt { get; set; } = DateTime.UtcNow;
    public DateTime? LastSyncAt { get; set; }
    public string? LastError { get; set; }

    public string Url => $"https://{Host}/share/d/{Guid}";
}

public sealed class Setting
{
    public string Key { get; set; } = "";
    public string Value { get; set; } = "";
}

/// <summary>One sync of one or more documents.</summary>
public sealed class SyncRun
{
    public int Id { get; set; }
    public DateTime StartedAt { get; set; }
    public DateTime? FinishedAt { get; set; }
    /// <summary>"schedule", "manual", "added"…</summary>
    public string Trigger { get; set; } = "";
    public int Docs { get; set; }
    public int Failed { get; set; }
    public List<ChangeEntry> Changes { get; set; } = [];
}

/// <summary>A page that was new, updated, renamed or removed in a sync (What's new).</summary>
public sealed class ChangeEntry
{
    public int Id { get; set; }
    public int SyncRunId { get; set; }
    public string DocGuid { get; set; } = "";
    public string DocTitle { get; set; } = "";
    /// <summary>The document folder, relative to the courses folder.</summary>
    public string DocFolder { get; set; } = "";
    public string Kind { get; set; } = "";
    /// <summary>The page, relative to the document folder.</summary>
    public string Path { get; set; } = "";
    /// <summary>Unified diff of the page text, when it was updated.</summary>
    public string? Diff { get; set; }
}

public sealed class LecternDb(DbContextOptions<LecternDb> options) : DbContext(options)
{
    public DbSet<AppUser> Users => Set<AppUser>();
    public DbSet<Doc> Docs => Set<Doc>();
    public DbSet<Setting> Settings => Set<Setting>();
    public DbSet<SyncRun> SyncRuns => Set<SyncRun>();
    public DbSet<ChangeEntry> Changes => Set<ChangeEntry>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        b.Entity<AppUser>().HasIndex(u => u.Username).IsUnique();
        b.Entity<AppUser>().Property(u => u.Username).UseCollation("NOCASE");
        b.Entity<Doc>().HasIndex(d => d.Guid).IsUnique();
        b.Entity<Setting>().HasKey(s => s.Key);
        b.Entity<SyncRun>().HasIndex(r => r.StartedAt);
        b.Entity<SyncRun>().HasMany(r => r.Changes).WithOne().HasForeignKey(c => c.SyncRunId).OnDelete(DeleteBehavior.Cascade);
        b.Entity<ChangeEntry>().HasIndex(c => c.DocGuid);
    }
}

/// <summary>Used by `dotnet ef` to create migrations.</summary>
public sealed class DesignTimeFactory : IDesignTimeDbContextFactory<LecternDb>
{
    public LecternDb CreateDbContext(string[] args) =>
        new(new DbContextOptionsBuilder<LecternDb>().UseSqlite("Data Source=design.db").Options);
}
