using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using Lectern.Data;
using Microsoft.EntityFrameworkCore;

namespace Lectern.Services;

/// <summary>When syncs run by themselves.</summary>
public sealed record Schedule
{
    /// <summary>"daily", "weekdays" or "every".</summary>
    public string Mode { get; init; } = "daily";
    public List<string> Times { get; init; } = ["09:00"];
    public double Hours { get; init; } = 6;
    public bool Enabled { get; init; } = true;

    /// <summary>"9:05" or "09:05" (24-hour clock).</summary>
    public static bool TryParseTime(string? text, out TimeSpan time)
    {
        time = default;
        var parts = (text ?? "").Trim().Split(':');
        if (parts.Length is < 2 or > 3 || !int.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out var h)
            || !int.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out var m) || parts[1].Length != 2
            || h > 23 || m > 59) return false;
        time = new TimeSpan(h, m, 0);
        return true;
    }

    private static TimeSpan At(string t) => TryParseTime(t, out var ts) ? ts : new TimeSpan(9, 0, 0);

    public string Describe()
    {
        var state = Enabled ? "on" : "off";
        if (Mode == "every") return $"Every {Hours:0.##} hour(s) ({state})";
        var when = Mode == "weekdays" ? "Weekdays (Mon–Fri)" : "Every day";
        return $"{when} at {string.Join(", ", Times)} ({state}, {TimeZoneInfo.Local.Id} time)";
    }

    /// <summary>The next scheduled sync, or null when automatic syncing is off.</summary>
    public DateTime? Next(DateTime now, DateTime? lastRun)
    {
        if (!Enabled) return null;
        if (Mode == "every")
        {
            var next = (lastRun ?? now).AddHours(Hours);
            return next > now ? next : now;
        }
        for (var ahead = 0; ahead < 8; ahead++)
        {
            var day = now.Date.AddDays(ahead);
            if (Mode == "weekdays" && day.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) continue;
            foreach (var t in Times.Order(StringComparer.Ordinal))
            {
                var moment = day + At(t);
                if (moment > now) return moment;
            }
        }
        return null;
    }

    /// <summary>The most recent scheduled moment at or before <paramref name="now"/> (daily and weekdays).</summary>
    public DateTime? LastDue(DateTime now)
    {
        for (var back = 0; back < 8; back++)
        {
            var day = now.Date.AddDays(-back);
            if (Mode == "weekdays" && day.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) continue;
            DateTime? best = null;
            foreach (var t in Times)
            {
                var moment = day + At(t);
                if (moment <= now && (best is null || moment > best)) best = moment;
            }
            if (best is not null) return best;
        }
        return null;
    }
}

/// <summary>What happens after each sync.</summary>
public sealed record SyncOptions
{
    /// <summary>Rebuild the PDF of each changed document.</summary>
    public bool Pdf { get; init; } = true;
    /// <summary>Save a history snapshot.</summary>
    public bool History { get; init; } = true;
    /// <summary>Download video and audio files uploaded to a document.</summary>
    public bool Videos { get; init; } = true;
}

/// <summary>Schedule and options, stored in the database.</summary>
public sealed class SettingsService(IDbContextFactory<LecternDb> dbFactory)
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web) { DefaultIgnoreCondition = JsonIgnoreCondition.Never };

    public event Action? Changed;

    public Task<Schedule> ScheduleAsync() => GetAsync("schedule", new Schedule());
    public Task SaveScheduleAsync(Schedule s) => SetAsync("schedule", s);
    public Task<SyncOptions> OptionsAsync() => GetAsync("options", new SyncOptions());
    public Task SaveOptionsAsync(SyncOptions o) => SetAsync("options", o);

    /// <summary>When the scheduler last started a sync.</summary>
    public async Task<DateTime?> LastScheduledRunAsync()
    {
        var v = await GetRawAsync("last-scheduled-run");
        return v is not null && DateTime.TryParse(v, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var d) ? d : null;
    }

    public Task SetLastScheduledRunAsync(DateTime when) => SetRawAsync("last-scheduled-run", when.ToString("O", CultureInfo.InvariantCulture));

    private async Task<T> GetAsync<T>(string key, T fallback)
    {
        var v = await GetRawAsync(key);
        if (v is null) return fallback;
        try { return JsonSerializer.Deserialize<T>(v, Json) ?? fallback; }
        catch (JsonException) { return fallback; }
    }

    private Task SetAsync<T>(string key, T value) => SetRawAsync(key, JsonSerializer.Serialize(value, Json));

    private async Task<string?> GetRawAsync(string key)
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        return (await db.Settings.AsNoTracking().FirstOrDefaultAsync(s => s.Key == key))?.Value;
    }

    private async Task SetRawAsync(string key, string value)
    {
        await using var db = await dbFactory.CreateDbContextAsync();
        var row = await db.Settings.FirstOrDefaultAsync(s => s.Key == key);
        if (row is null) db.Settings.Add(new Setting { Key = key, Value = value });
        else row.Value = value;
        await db.SaveChangesAsync();
        Changed?.Invoke();
    }
}
