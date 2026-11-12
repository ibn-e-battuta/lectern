using Lectern.Services;
using Xunit;

namespace Lectern.Tests;

public class ScheduleTests
{
    [Theory]
    [InlineData("08:30", 8, 30)]
    [InlineData("21:00", 21, 0)]
    [InlineData("9:05", 9, 5)]
    public void Parses_times(string text, int h, int m)
    {
        Assert.True(Schedule.TryParseTime(text, out var t));
        Assert.Equal(new TimeSpan(h, m, 0), t);
    }

    [Theory]
    [InlineData("24:00")]
    [InlineData("9")]
    [InlineData("nine")]
    public void Rejects_bad_times(string text) => Assert.False(Schedule.TryParseTime(text, out _));

    [Fact]
    public void Weekdays_skip_the_weekend()
    {
        var s = new Schedule { Mode = "weekdays", Times = ["09:00"] };
        var friday = new DateTime(2026, 11, 13, 10, 0, 0);
        Assert.Equal(new DateTime(2026, 11, 16, 9, 0, 0), s.Next(friday, null));
        Assert.Equal(new DateTime(2026, 11, 13, 9, 0, 0), s.LastDue(friday));
        Assert.Equal(new DateTime(2026, 11, 13, 9, 0, 0), s.LastDue(new DateTime(2026, 11, 15, 12, 0, 0)));
    }

    [Fact]
    public void Every_few_hours_counts_from_the_last_run()
    {
        var s = new Schedule { Mode = "every", Hours = 6 };
        var now = new DateTime(2026, 11, 12, 12, 0, 0);
        Assert.Equal(new DateTime(2026, 11, 12, 16, 0, 0), s.Next(now, new DateTime(2026, 11, 12, 10, 0, 0)));
        Assert.Equal(now, s.Next(now, new DateTime(2026, 11, 12, 1, 0, 0)));
        Assert.Null((s with { Enabled = false }).Next(now, null));
    }
}
