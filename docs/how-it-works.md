# How Mispy works

About 2 500 lines of Python, two short Swift files and a LaTeX preamble. This page
is for when you want to change something, or when you want to know what is running
on your machine before you trust it with a meeting.

## Where everything lives

**macOS**

```
~/.local/lib/mispy/           the program and its private virtualenv
         mispy/               the Python package
         style/minutes.tex    the house style of the PDF
~/.local/bin/mispy            the command
             mispy-audio      Swift, records what the Mac is playing (ScreenCaptureKit)
             mispy-cal        Swift, reads the diary (EventKit)
~/.local/share/mispy/         whisper.cpp and the speech models
~/.local/state/mispy/         job.json and work/ — transient, wiped after every meeting
~/.config/mispy/              config.toml, glossary.txt
```

**Windows**

```
%LOCALAPPDATA%\Mispy\lib\     the program and its private virtualenv
%LOCALAPPDATA%\Mispy\bin\     mispy.cmd, the only thing added to your PATH
%LOCALAPPDATA%\Mispy\models\  the speech model
%LOCALAPPDATA%\Mispy\state\   job.json and work\ — transient, wiped after every meeting
%APPDATA%\Mispy\              config.toml, glossary.txt
```

No Swift helpers on Windows: both jobs are done from Python. Nothing is written
outside those folders, except the documents in your meetings folder. Nothing is
installed for other users, no service is registered, and no admin rights are used
at any point.

## The modules

| file | responsible for |
|---|---|
| `cli.py` | Every command. |
| `wizard.py` | `mispy setup`. |
| `host.py` | Everything the two operating systems disagree about: where files go, how a process is killed, how a notification is shown. Nothing else in the codebase branches on the platform. |
| `capture.py` | One interface over the two recorders. |
| `capture_mac.py` | ffmpeg plus the ScreenCaptureKit helper. |
| `capture_win.py` | WASAPI, both tracks, through `soundcard`. Also the detached recorder process itself. |
| `audio.py` | WAV arithmetic in numpy: resampling, levelling, mixing, joining. |
| `recorder.py` | macOS only — ffmpeg: resolving the input by name, clean start and stop. |
| `meeting.py` | What the diary knows; where a meeting's folder goes. |
| `calendar_win.py` | Outlook, over COM. |
| `transcribe.py` | whisper.cpp, and turning its SRT into a timed transcript. |
| `minutes.py` | The prompt, the call to Claude, and parsing the reply. |
| `render.py` | Markdown and LaTeX out of the same structure. |
| `attachments.py` | Reading whatever you dropped in the folder. |
| `pipeline.py` | The stages after `stop`, resumably. |
| `state.py` | One JSON file, written atomically. The job's entire memory. |
| `tui.py` | The app, and the creature. |

## The two recorders

### On macOS

The microphone goes through ffmpeg:

```
nohup caffeinate -i ffmpeg -f avfoundation -i ":MacBook Air Microphone" \
      -ar 16000 -ac 1 -c:a pcm_s16le mic-01.wav
```

`caffeinate` stops the Mac idling to sleep and dies with its child. `nohup` matters
because a stray `SIGHUP` — closing the terminal — leaves ffmpeg alive but silently
stops it writing, which turns a two-hour meeting into an empty file. Stopping sends
`SIGINT` to the whole **process group**: the recorded pid is the wrapper, and
ffmpeg, which must flush the WAV header, is its child.

The call audio goes through `mispy-audio`, which opens an `SCStream` with
`capturesAudio = true`, `excludesCurrentProcessAudio = true` and a 2×2 pixel video
configuration that nothing ever reads. Audio sample buffers are written straight to
a WAV. It stops on `SIGINT` and closes the file so the header is correct.

macOS files that permission under *Screen Recording*, and asks only once. After a
refusal only System Settings will change it, which is why `mispy-audio --check`
exists and why `mispy doctor` reports it.

### On Windows

Neither track goes through ffmpeg, and there is no virtual audio cable. `soundcard`
opens the microphone and, separately, a **WASAPI loopback** on the chosen output —
which is how Windows lets a program listen to what it is playing. Both are written
to 48 kHz mono WAVs by one detached process, which stops when a sentinel file
appears next to its output. Signals on Windows are not worth the trouble: there is
no `SIGINT` you can rely on reaching a detached child, and a hard kill leaves a WAV
with a zero-length header.

That process also calls `SetThreadExecutionState`, which is Windows' answer to
`caffeinate`: the machine will not idle to sleep while a recording is open.

### Mixing, on both

**Per segment, not per track.** The two recorders do not start on the same
millisecond. Mixing `mic.wav` against `sys.wav` as whole tracks would hold that
offset for the length of the meeting; mixing `mic-03.wav` against `sys-03.wav` and
then concatenating keeps each error inside its own segment.

macOS does it with ffmpeg: `amix=duration=longest:normalize=0` — without
`normalize=0`, ffmpeg halves the level of both inputs — followed by `dynaudnorm`.
Windows does it with numpy: 48 kHz down to 16 kHz by averaging each group of three
samples, which is both the decimation and the low-pass it needs, then each track
brought to the same RMS before they are summed. Either way the reason is the same:
a voice through the speakers and a voice in the room almost never arrive at the
same loudness, and whichever is quieter is the one the recogniser loses.

## The two engines

| | whisper.cpp | faster-whisper |
|---|---|---|
| installed by | one `cmake`, on a Mac | one `pip install` |
| runs on | the GPU, through Metal | the CPU, int8 — or CUDA if there is any |
| model | a `ggml-*.bin` file | a name, downloaded on first use |
| an hour of meeting | about 7 minutes | 10 to 20 minutes on a work laptop |

Which one runs is decided by the model in your config: a path ending in `.bin`
means whisper.cpp, any other string means faster-whisper. Not by what happens to be
installed — a config file that behaves differently on two machines is a config file
you cannot reason about.

## Outlook

`calendar_win.py` talks to classic Outlook over COM, as you, with no registration
and no consent screen. Two things about it are less obvious than they look.

**Recurring appointments.** `Items.IncludeRecurrences = True` makes the collection
expand a weekly meeting into an infinite sequence; iterating it without a bound
never returns. Mispy sorts by `[Start]`, restricts to the window, and stops after
400 items regardless.

**The date format in a restriction.** Outlook parses the filter with the machine's
short date format, which cannot be known from the outside — `08/10/2026` is October
8th in Paris and August 10th in New York. Mispy tries the plausible formats in turn
and then filters again in Python on the real `datetime`, so a filter that was too
wide is corrected and one that was too narrow is retried.

## The minutes

The model is given the timed transcript, the date, the attendee list if the diary
had one, and the text of anything in the folder. It answers with a fixed set of
markers:

```
::title::  ::summary::  ::decisions::  ::actions::
::discussion::  ::questions::  ::followups::
```

The markers are in English whatever language the minutes are written in, which is
what makes parsing them language-independent. Actions come back one per line in a
fixed shape — `owner: … | what: … | when: …` — so the table can be built rather than
trusted, and a line the model wrote freehand still survives as an action with no
owner.

Three rules in the prompt matter more than the format:

- Only what was said. No decision, figure or deadline added because a meeting like
  this usually has one.
- Attribution only when a name was spoken or the attendee list leaves no doubt.
- Garbled passages recovered from context where possible, marked `[unclear]` where
  not, and left out rather than guessed.

## Resuming, and failing

The stage lives in `~/.local/state/mispy/job.json`, written through a temporary file
and `os.replace`, so a Mac that sleeps mid-write never leaves half a job behind:

```
idle → recording ⇄ paused → details → transcribing → writing → typesetting → done
                                                                    ↓
                                                                  failed
```

Every stage checks whether its own output already exists before doing the work.
Close the lid during transcription, open it at home, and it carries on from the
transcript. On failure the audio is deliberately **kept** — that is the only case
where sound survives — and `mispy stop` runs the rest again.

## Changing things

| you want to | do this |
|---|---|
| change the sections of the minutes | `RULES` in `mispy/minutes.py` |
| change how the PDF looks | `~/.local/lib/mispy/style/minutes.tex` |
| write minutes in a fixed language | `[minutes] language = "en"` |
| shorter or longer minutes | `[minutes] style = "brief"` / `"verbatim"` |
| keep the recording | `[privacy] keep_audio = true` |
| stop names being mangled | `~/.config/mispy/glossary.txt` |
| record only the call | `[audio] capture = "system"` |
| choose which files you get | `[output] formats = ["md", "docx", "pdf"]` |
| force one engine | `[transcribe] engine = "faster_whisper"` |
| use the API instead of your subscription | export `ANTHROPIC_API_KEY`, set `backend = "api"` |

After editing anything under `mispy/` in a clone, re-run `./install.sh`.

## Known limits

- **No speaker diarisation.** Voices are not separated. Attribution comes from
  names being spoken, and is left blank otherwise, on purpose.
- **Recording stops when the lid closes.** Clamshell sleep is enforced by the
  firmware on Apple Silicon; `caffeinate` prevents *idle* sleep, not that.
- **Attachments are read as text.** A deck's diagrams do not reach the model.
- **One meeting at a time.**
- **The new Outlook has no COM interface.** Classic Outlook, a published `.ics`,
  or name your meetings yourself.
- **Windows transcribes on the CPU**, so an hour of meeting is ten to twenty
  minutes rather than seven. `large-v3-turbo` is the sensible default there.
- **The minutes are only as good as the transcript**, which is why the glossary is
  the highest-leverage file in the whole project.
