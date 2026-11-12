using System.Reflection;

namespace Lectern.Services;

/// <summary>Settings from appsettings.json or <c>Lectern__…</c> environment variables.</summary>
public sealed class LecternOptions
{
    /// <summary>The courses folder documents are written into.</summary>
    public string CoursesRoot { get; set; } = "/courses";

    /// <summary>Lectern's own data: database, sign-in keys, history and the log.</summary>
    public string ConfigFolder { get; set; } = "config";

    /// <summary>Behind a reverse proxy: use the client address and scheme it forwards.</summary>
    public bool TrustForwardedHeaders { get; set; }

    /// <summary>Chrome, Chromium or Edge for PDF export. Found automatically when empty.</summary>
    public string? Chromium { get; set; }

    /// <summary>Resolved absolute paths (set at startup).</summary>
    public string CoursesPath { get; set; } = "";
    public string ConfigPath { get; set; } = "";
}

public static class AppInfo
{
    public static string Version { get; } =
        typeof(AppInfo).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0] ?? "0.0.0";
}
