namespace Lectern.Services;

/// <summary>Lectern's activity log: kept in memory for the Activity page and appended to <c>lectern.log</c>.</summary>
public sealed class ActivityLog(LecternOptions options, ILogger<ActivityLog> logger)
{
    private const int Keep = 1000;
    private readonly LinkedList<string> _lines = new();
    private readonly Lock _gate = new();
    private bool _loaded;

    public event Action? Changed;

    private string FilePath => Path.Combine(options.ConfigPath, "lectern.log");

    public void Info(string message) => Write(message, LogLevel.Information);
    public void Warn(string message) => Write("WARN: " + message, LogLevel.Warning);
    public void Error(string message) => Write("ERROR: " + message, LogLevel.Error);

    private void Write(string message, LogLevel level)
    {
        var line = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}";
        logger.Log(level, "{Message}", message);
        lock (_gate)
        {
            EnsureLoaded();
            _lines.AddLast(line);
            while (_lines.Count > Keep) _lines.RemoveFirst();
            try
            {
                File.AppendAllText(FilePath, line + "\n");
                // Keep the file from growing forever.
                if (new FileInfo(FilePath).Length > 5_000_000) File.WriteAllLines(FilePath, _lines);
            }
            catch (IOException) { }
        }
        Changed?.Invoke();
    }

    public List<string> Tail(int count)
    {
        lock (_gate)
        {
            EnsureLoaded();
            return _lines.Skip(Math.Max(0, _lines.Count - count)).ToList();
        }
    }

    private void EnsureLoaded()
    {
        if (_loaded) return;
        _loaded = true;
        try
        {
            if (File.Exists(FilePath))
                foreach (var l in File.ReadLines(FilePath).TakeLast(Keep)) _lines.AddLast(l);
        }
        catch (IOException) { }
    }
}
