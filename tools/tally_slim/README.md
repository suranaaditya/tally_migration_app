# TallySlim — Prepare Tally XML for RGI Migration

## What it does

Converts your full Tally All Masters XML export into a smaller version
that the RGI Migration system can accept. Typical reduction:
220 MB to 3 MB.

## When to use it

Run this **after** exporting from Tally but **before** uploading to the
RGI migration system in your browser.

## How to use it

1. Double-click `TallySlim.exe`
2. Click **Browse...** next to "Input file" and pick your Tally XML export
3. The **Output file** field fills in automatically — leave it or change
   if you prefer a different name or folder
4. Click **Convert**
5. Wait ~30 seconds for the conversion (longer on very large files)
6. Upload the **output** file (the smaller one) to the RGI migration
   system

## First-time Windows warning (one of two variants)

### Variant A — SmartScreen warning (most common)

The first time you run TallySlim on a new computer, Windows may show:

> Windows protected your PC
> Windows Defender SmartScreen prevented an unrecognized app from
> starting.

This is normal for newly-built tools. To proceed:

1. Click **More info**
2. Click **Run anyway**

This happens once per computer and then goes away.

### Variant B — Antivirus blocks the app entirely

On some machines your antivirus (Windows Defender, McAfee, Norton,
Kaspersky, or similar) is more aggressive and refuses to launch
TallySlim at all, with a message like:

> Threat detected
> This file contains a virus or potentially unwanted software.

or silently quarantines the file so it disappears from the folder.

This is a **false alarm**. Newly-built tools often get flagged by AV
heuristics until enough people have run them that the AV vendor
learns they're safe. The tool is not infected.

To run it:

1. Ask your IT team (or someone with admin rights on the machine)
   to add TallySlim.exe to your antivirus's exclusion list. Exact
   steps vary by AV product — in McAfee it's
   **Real-Time Scanning -> Excluded Files -> Add file**; in Windows
   Defender it's **Virus & threat protection -> Manage settings ->
   Exclusions -> Add exclusion**.
2. If the file has already been quarantined, IT can restore it from
   the AV's quarantine/history view and whitelist it at the same time.
3. If neither works on your machine, send the quarantine notification
   to your project administrator.

If none of the above are available, contact support with the AV
product name and the exact error message.

## If something goes wrong

The tool writes a log file next to your output file (same name, with a
`.log` extension). If the tool fails or produces unexpected results,
send this log file to your project administrator.

Common issues:

- **"Input file missing"** — the XML file you picked has been moved or
  deleted since you chose it. Re-browse.
- **"Input does not look like a Tally All Masters XML"** — re-export
  from Tally using **Export -> Masters -> All Masters** with the
  **Closing as opening** option enabled.
- **"Conversion failed"** dialog with a log path — send the log file to
  support.

## Support

Contact: aditya.surana@jewonline.in
