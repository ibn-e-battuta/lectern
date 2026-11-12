namespace Lectern.Components;

public static class Format
{
    /// <summary>"just now", "5 min ago", "3 h ago", "2 d ago" or "never".</summary>
    public static string Ago(DateTime? when)
    {
        if (when is not { } w) return "never";
        var local = w.Kind == DateTimeKind.Utc ? w.ToLocalTime() : w;
        var m = (int)Math.Round((DateTime.Now - local).TotalMinutes);
        if (m < 1) return "just now";
        if (m < 60) return $"{m} min ago";
        var h = (int)Math.Round(m / 60.0);
        return h < 24 ? $"{h} h ago" : $"{(int)Math.Round(h / 24.0)} d ago";
    }

    /// <summary>"Today 21:00", "Tomorrow 09:00" or "Mon 3 Nov, 09:00".</summary>
    public static string When(DateTime when)
    {
        var today = DateTime.Today;
        if (when.Date == today) return $"today {when:HH:mm}";
        if (when.Date == today.AddDays(1)) return $"tomorrow {when:HH:mm}";
        return when.ToString("ddd d MMM, HH:mm");
    }
}
