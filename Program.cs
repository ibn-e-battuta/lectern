using Lectern.Components;
using Lectern.Data;
using Lectern.Services;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.AspNetCore.StaticFiles;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// ---------- options ----------

var options = builder.Configuration.GetSection("Lectern").Get<LecternOptions>() ?? new LecternOptions();
var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
options.CoursesPath = Path.GetFullPath(options.CoursesRoot.StartsWith('~') ? home + options.CoursesRoot[1..] : options.CoursesRoot,
    builder.Environment.ContentRootPath);
options.ConfigPath = Path.GetFullPath(options.ConfigFolder, builder.Environment.ContentRootPath);
Directory.CreateDirectory(options.ConfigPath);
builder.Services.AddSingleton(options);

// ---------- data ----------

var connection = builder.Configuration.GetConnectionString("Lectern") ?? $"Data Source={Path.Combine(options.ConfigPath, "lectern.db")}";
builder.Services.AddDbContextFactory<LecternDb>(o => o.UseSqlite(connection));

// ---------- services ----------

builder.Services.AddHttpClient<BitdocsClient>(c => c.Timeout = TimeSpan.FromMinutes(10));
builder.Services.AddSingleton<ActivityLog>();
builder.Services.AddSingleton<SettingsService>();
builder.Services.AddSingleton<DocsService>();
builder.Services.AddSingleton<DocReader>();
builder.Services.AddSingleton<PdfBuilder>();
builder.Services.AddSingleton<HistoryService>();
builder.Services.AddSingleton<SearchService>();
builder.Services.AddSingleton<LinkScanner>();
builder.Services.AddSingleton<SyncRunner>();
builder.Services.AddSingleton<UserService>();
builder.Services.AddHostedService<Scheduler>();

// ---------- sign-in ----------

// Persisted keys keep everyone signed in across restarts. The application name is part of the key.
builder.Services.AddDataProtection().SetApplicationName("Lectern")
    .PersistKeysToFileSystem(Directory.CreateDirectory(Path.Combine(options.ConfigPath, "keys")));

builder.Services.AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(o =>
    {
        o.Cookie.Name = "lectern.auth";
        o.Cookie.HttpOnly = true;
        o.Cookie.SameSite = SameSiteMode.Lax;
        o.LoginPath = "/login";
        o.ExpireTimeSpan = TimeSpan.FromDays(60);
        o.SlidingExpiration = true;
        o.Events.OnValidatePrincipal = ctx => ctx.HttpContext.RequestServices.GetRequiredService<UserService>().ValidatePrincipalAsync(ctx);
        o.Events.OnRedirectToLogin = ctx =>
        {
            if (ctx.Request.Path.StartsWithSegments("/files")) ctx.Response.StatusCode = StatusCodes.Status401Unauthorized;
            else ctx.Response.Redirect(ctx.RedirectUri);
            return Task.CompletedTask;
        };
    });
builder.Services.AddAuthorizationBuilder()
    .SetFallbackPolicy(new Microsoft.AspNetCore.Authorization.AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build());
builder.Services.AddCascadingAuthenticationState();

if (options.TrustForwardedHeaders)
{
    builder.Services.Configure<ForwardedHeadersOptions>(o =>
    {
        o.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto | ForwardedHeaders.XForwardedHost;
        o.KnownIPNetworks.Clear();
        o.KnownProxies.Clear();
    });
}

builder.Services.AddRazorComponents().AddInteractiveServerComponents();

var app = builder.Build();

// ---------- startup ----------

await using (var db = await app.Services.GetRequiredService<IDbContextFactory<LecternDb>>().CreateDbContextAsync())
{
    await db.Database.MigrateAsync();
}
await app.Services.GetRequiredService<UserService>().BootstrapAdminAsync(
    builder.Configuration["LECTERN_ADMIN_USER"], builder.Configuration["LECTERN_ADMIN_PASSWORD"]);
if (!Directory.Exists(options.CoursesPath))
    app.Logger.LogWarning("The courses folder {Path} doesn't exist. Mount it, or set Lectern__CoursesRoot.", options.CoursesPath);

// ---------- pipeline ----------

if (options.TrustForwardedHeaders) app.UseForwardedHeaders();
if (!app.Environment.IsDevelopment()) app.UseExceptionHandler("/error", createScopeForErrors: true);
app.UseAuthentication();
app.UseAuthorization();
app.UseAntiforgery();

app.MapStaticAssets().AllowAnonymous();

app.MapGet("/health", () => Results.Ok(new { status = "ok", version = AppInfo.Version })).AllowAnonymous();

// Files of synced documents (images, attachments, PDFs, Markdown) for the reader. Nothing else in the courses folder.
var contentTypes = new FileExtensionContentTypeProvider();
contentTypes.Mappings[".md"] = "text/plain; charset=utf-8";
app.MapGet("/files/{**path}", async (string path, DocsService docs) =>
{
    var full = Path.GetFullPath(Path.Combine(options.CoursesPath, path));
    var folders = (await docs.ListAsync()).Select(docs.FolderOf).OfType<string>().Select(f => Path.GetFullPath(f).TrimEnd('/') + "/");
    if (!folders.Any(f => full.StartsWith(f, StringComparison.Ordinal)) || !File.Exists(full)
        || PyCompat.RelPath(full, options.CoursesPath).Split('/').Any(s => s.StartsWith('.')))
        return Results.NotFound();
    if (!contentTypes.TryGetContentType(full, out var type)) type = "application/octet-stream";
    return Results.File(full, type, enableRangeProcessing: true);
});

app.MapPost("/logout", async (HttpContext ctx) =>
{
    await ctx.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
    return Results.Redirect("login");
});

app.MapRazorComponents<App>().AddInteractiveServerRenderMode();

app.Run();
