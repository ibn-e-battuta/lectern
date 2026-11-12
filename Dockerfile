# syntax=docker/dockerfile:1.7
#
# Lectern: keeps bitdocs course documents synced into your courses folder.
#   docker build -t lectern .
#   docker run -p 8790:8080 -v /path/to/courses:/courses -v ./config:/config lectern

# ---------- app ----------
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
# Restore first for layer caching. Publish restores again once the .razor files are present:
# the SDK only adds Blazor's framework script (_framework/blazor.web.js) when it sees components.
COPY Lectern.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish Lectern.csproj -c Release -o /app /p:UseAppHost=false \
 && test -n "$(grep -l '_framework/blazor.web' /app/Lectern.staticwebassets.endpoints.json)"

# ---------- runtime ----------
FROM mcr.microsoft.com/dotnet/aspnet:10.0-alpine

LABEL org.opencontainers.image.title="Lectern" \
      org.opencontainers.image.description="Keeps bitdocs course documents synced into your courses folder, with a reader, search, PDFs and history"

# chromium: PDF export · fonts: readable PDFs · tzdata: local schedule times · curl: health check
# (Alpine: its chromium package works in containers, unlike Ubuntu's snap-only one.)
RUN apk add --no-cache chromium font-noto font-noto-emoji font-dejavu tzdata curl ca-certificates

WORKDIR /app
COPY --from=build /app .

ENV ASPNETCORE_HTTP_PORTS=8080 \
    Lectern__CoursesRoot=/courses \
    Lectern__ConfigFolder=/config \
    Lectern__Chromium=/usr/bin/chromium-browser \
    TZ=Etc/UTC

RUN mkdir -p /config /courses && chown app:app /config
VOLUME ["/config"]
EXPOSE 8080
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8080/health > /dev/null || exit 1

ENTRYPOINT ["dotnet", "Lectern.dll"]
