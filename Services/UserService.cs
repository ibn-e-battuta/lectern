using System.Collections.Concurrent;
using System.Security.Claims;
using Lectern.Data;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

namespace Lectern.Services;

public static class Claims
{
    public const string UserId = ClaimTypes.NameIdentifier;
    public const string Stamp = "lectern:stamp";

    public static int? GetUserId(this ClaimsPrincipal user) => int.TryParse(user.FindFirstValue(UserId), out var id) ? id : null;
}

/// <summary>Accounts, passwords and sign-in checks.</summary>
public sealed class UserService(IDbContextFactory<LecternDb> dbFactory, ILogger<UserService> logger)
{
    public const int MinPasswordLength = 8;

    private static readonly PasswordHasher<AppUser> Hasher = new();
    private static readonly Lazy<string> DummyHash = new(() => Hasher.HashPassword(new AppUser(), Guid.NewGuid().ToString()));
    private readonly ConcurrentDictionary<int, string> _stamps = new();
    private readonly ConcurrentDictionary<string, (int Failures, DateTime WindowStart)> _failures = new();
    private int? _count;

    public async Task<int> CountAsync()
    {
        if (_count is { } c) return c;
        await using var db = await dbFactory.CreateDbContextAsync();
        return (_count = await db.Users.CountAsync()).Value;
    }

    public async Task<(AppUser? User, string? Error)> CreateAsync(string username, string password, bool isAdmin)
    {
        username = username.Trim();
        if (username.Length is 0 or > 64) return (null, "Choose a username (up to 64 characters).");
        if (password.Length < MinPasswordLength) return (null, $"The password needs at least {MinPasswordLength} characters.");
        await using var db = await dbFactory.CreateDbContextAsync();
        if (await db.Users.AnyAsync(u => u.Username == username)) return (null, "That username is taken.");
        var user = new AppUser { Username = username, IsAdmin = isAdmin };
        user.PasswordHash = Hasher.HashPassword(user, password);
        db.Users.Add(user);
        await db.SaveChangesAsync();
        _count = null;
        return (user, null);
    }

    /// <summary>Creates the first admin from environment variables, if set and no account exists yet.</summary>
    public async Task BootstrapAdminAsync(string? username, string? password)
    {
        if (string.IsNullOrWhiteSpace(username) || string.IsNullOrEmpty(password) || await CountAsync() > 0) return;
        var (_, err) = await CreateAsync(username, password, isAdmin: true);
        if (err is not null) logger.LogError("Could not create the admin from environment variables: {Error}", err);
        else logger.LogInformation("Created the admin account '{User}' from environment variables", username);
    }

    /// <summary>Blocks a username/address pair for 15 minutes after 8 wrong passwords.</summary>
    public bool IsLockedOut(string key) =>
        _failures.TryGetValue(key, out var f) && f.Failures >= 8 && DateTime.UtcNow - f.WindowStart < TimeSpan.FromMinutes(15);

    public async Task<AppUser?> VerifyAsync(string username, string password, string throttleKey)
    {
        if (IsLockedOut(throttleKey)) return null;
        await using var db = await dbFactory.CreateDbContextAsync();
        var user = await db.Users.FirstOrDefaultAsync(u => u.Username == username.Trim());
        var ok = false;
        if (user is not null)
        {
            var result = Hasher.VerifyHashedPassword(user, user.PasswordHash, password);
            ok = result != PasswordVerificationResult.Failed;
            if (result == PasswordVerificationResult.SuccessRehashNeeded) user.PasswordHash = Hasher.HashPassword(user, password);
        }
        else Hasher.VerifyHashedPassword(new AppUser(), DummyHash.Value, password); // same time as a real check

        if (!ok)
        {
            _failures.AddOrUpdate(throttleKey, (1, DateTime.UtcNow), (_, f) =>
                DateTime.UtcNow - f.WindowStart > TimeSpan.FromMinutes(15) ? (1, DateTime.UtcNow) : (f.Failures + 1, f.WindowStart));
            return null;
        }
        _failures.TryRemove(throttleKey, out _);
        user!.LastLoginAt = DateTime.UtcNow;
        await db.SaveChangesAsync();
        return user;
    }

    public async Task<string?> ChangePasswordAsync(int userId, string current, string next)
    {
        if (next.Length < MinPasswordLength) return $"The new password needs at least {MinPasswordLength} characters.";
        await using var db = await dbFactory.CreateDbContextAsync();
        var user = await db.Users.FirstOrDefaultAsync(u => u.Id == userId);
        if (user is null) return "Unknown account.";
        if (Hasher.VerifyHashedPassword(user, user.PasswordHash, current) == PasswordVerificationResult.Failed) return "The current password is wrong.";
        user.PasswordHash = Hasher.HashPassword(user, next);
        user.SecurityStamp = Guid.NewGuid().ToString("N"); // signs out other sessions
        await db.SaveChangesAsync();
        _stamps.TryRemove(userId, out _);
        return null;
    }

    public static ClaimsPrincipal Principal(AppUser user) => new(new ClaimsIdentity(
    [
        new Claim(Claims.UserId, user.Id.ToString()),
        new Claim(ClaimTypes.Name, user.Username),
        new Claim(Claims.Stamp, user.SecurityStamp),
    ], CookieAuthenticationDefaults.AuthenticationScheme));

    /// <summary>Rejects cookies issued before a password change or for deleted accounts.</summary>
    public async Task ValidatePrincipalAsync(CookieValidatePrincipalContext ctx)
    {
        var id = ctx.Principal?.GetUserId();
        var stamp = ctx.Principal?.FindFirstValue(Claims.Stamp);
        if (id is null || stamp is null) { ctx.RejectPrincipal(); return; }
        if (!_stamps.TryGetValue(id.Value, out var current))
        {
            await using var db = await dbFactory.CreateDbContextAsync();
            current = await db.Users.Where(u => u.Id == id).Select(u => u.SecurityStamp).FirstOrDefaultAsync() ?? "";
            _stamps[id.Value] = current;
        }
        if (current != stamp)
        {
            ctx.RejectPrincipal();
            await ctx.HttpContext.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
        }
    }
}
