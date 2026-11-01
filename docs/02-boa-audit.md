# 02 - Boa and CGI audit

## Scope and method

The audit covers the web UI that ships in the `/customer` SquashFS image:
`Boa/0.94.13`, its `boa.conf`, and the 24 CGI programs in
`/customer/res/boa/cgi-bin/`. The live `boa.conf`, `mime.types`,
`css/main.css` and `index.html` were byte-identical to the firmware copies,
so the static analysis applies to the running device.

The work was static inspection plus targeted, non-destructive requests. It was
not a fuzzing campaign.

## Server configuration

```text
Port 80
User 0
Group 0
DocumentRoot /customer/res/boa
ScriptAlias /cgi-bin/ /customer/res/boa/cgi-bin/
DirectoryIndex index.html
MimeTypes /customer/res/boa/mime.types
```

Two omissions matter:

- there is no `DirectoryMaker`, so Boa does not spawn an indexer for
  directory URLs;
- there is no `Alias`, so there is no second filesystem root.

The `DirectoryIndex` only selects `index.html`. The `boa_indexer` helper is
not present in the image, which means directory-listing code paths are not
reachable even if a request asks for a directory.

Boa is configured with `User 0` and `Group 0`, so the server and its CGI
children run with root credentials.

## CGI inventory

All 24 CGI programs were reviewed. The relevant ones and their data flow:

| CGI | Request input | Sink | Result |
|---|---|---|---|
| `playUrl.cgi` | `url=`, `loop=` | `system()` with a fixed command | no injection |
| `media_control.cgi` | `control=` | `property_set()` | 10 fixed tokens only |
| `scan.cgi` | none | constant IPC messages | no request data |
| `connect.cgi`, `forget.cgi`, `connectSavedAp.cgi`, other network CGIs | network parameters | Unix-socket IPC | no shell |
| `settings.cgi` | form fields | config writers | no request-controlled shell |
| `password.cgi` | form fields | config writers | no request-controlled shell |
| `language.cgi` | form fields | config writers | no request-controlled shell |
| `overscan.cgi` | form fields | config writers | no request-controlled shell |
| `key.cgi` | key value | `/system/bin/tvcontrol -k %d` | numeric format |
| `debug.cgi` | none | fixed `setprop` | no request data |
| `ios_ver_set.cgi` | none | four fixed `fw_setenv` calls | no request data |
| `test.cgi` | none | hardcoded mount | fixed device and path |

### playUrl.cgi

`playUrl.cgi` is the most interesting case because the format strings are
unquoted:

```text
/system/bin/mediaplayertest -f %s
/system/bin/mediaplayertest -f %s -l
```

The `%s` argument is not the request value. The disassembly loads the literal
`/data/video_url` and passes it to `sprintf`, then calls `system`:

```asm
0001098c  ldr r2, [pc, #0x3c]   ; r2 = 0x10c48 -> "/data/video_url"
00010990  mov r0, r4            ; command buffer
00010998  ldrne r1, [pc, #0x34] ; "-f %s -l"
0001099c  ldreq r1, [pc, #0x34] ; "-f %s"
000109a0  bl sprintf@plt
000109a8  bl system@plt
```

The request's `url` value is written with `fwrite` into `/data/video_url`.
The shell command itself is constant. The unquoted `%s` is therefore inert.

### media_control.cgi

The only parameter is `control=`. It is split and compared against ten fixed
tokens (`switch`, `play`, `pause`, `stop`, `ffw`, `rew`, `next`, `pre`,
`vol_up`, `vol_down`) and then passed to `property_set`. No shell, no
executable path and no request-controlled format string are involved.

### scan.cgi and the network CGIs

`scan.cgi` does not read request data at all; it sends constant IPC messages.
The network configuration CGIs (`connect.cgi`, `forget.cgi`,
`connectSavedAp.cgi` and the related endpoints) use Unix-socket IPC rather
than a shell. Their effect is in the daemon that consumes the IPC, which is
outside the audited binaries.

### Settings and debug CGIs

`settings.cgi`, `password.cgi`, `language.cgi` and `overscan.cgi` write
configuration values through library calls; none of them reaches a shell with
a request-controlled string. `key.cgi` formats the key as a decimal integer
for `/system/bin/tvcontrol -k %d`. `debug.cgi` uses a fixed `setprop`.
`ios_ver_set.cgi` performs four fixed `fw_setenv` calls.

### test.cgi

`test.cgi` mounts a hardcoded device and path:

```text
/dev/block/sda1 -> /mnt/external_sd
```

The request does not select the device or the mount point.

## Negative results

The following vectors were checked and did not produce a request-controlled
shell sink or an escape from the document root:

- command injection through `playUrl.cgi` (`;`, command substitution,
  unquoted `%s`): the command argument is the literal `/data/video_url`;
- injection through `media_control.cgi`: only ten fixed tokens;
- request injection through `scan.cgi`: it reads no request data;
- shell execution through the network CGIs: they use Unix-socket IPC;
- request-controlled shell in `settings.cgi`, `password.cgi`,
  `language.cgi`, `overscan.cgi`, `key.cgi`, `debug.cgi` and
  `ios_ver_set.cgi`: no sink found;
- path traversal with `..`, `%00`, double encoding and `PATH_INFO`: no escape
  from `DocumentRoot` or the `ScriptAlias` root in the tested paths;
- directory listing and `boa_indexer`: the helper is not present and
  `DirectoryMaker` is not configured.

## Unknowns

- The daemons behind the Unix-socket IPC and the internals of `property_set`
  were not audited.
- The audit did not exhaustively test every vendor CGI with malformed input;
  it focused on request-to-sink data flow.
- The exact runtime credentials of CGI children are taken from `boa.conf`
  (`User 0`, `Group 0`) and were not measured on the device.
