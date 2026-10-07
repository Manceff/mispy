# How Mispy works

About 2 000 lines of Python, two short Swift files and a LaTeX preamble. This page
is for when you want to change something, or when you want to know what is running
on your machine before you trust it with a meeting.

## Where everything lives

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

Nothing else is written anywhere, except the `.md` and `.pdf` in your meetings
folder.

## The modules

| file | responsible for |
|---|---|
| `cli.py` | Every command. |
| `wizard.py` | `mispy setup`. |
| `capture.py` | Starting and stopping both recorders, and mixing their tracks. |
| `recorder.py` | ffmpeg: resolving the input by name, clean start and stop. |
| `meeting.py` | What the diary knows; where a meeting's folder goes. |
| `transcribe.py` | whisper.cpp, and turning its SRT into a timed transcript. |
| `minutes.py` | The prompt, the call to Claude, and parsing the reply. |
| `render.py` | Markdown and LaTeX out of the same structure. |
| `attachments.py` | Reading whatever you dropped in the folder. |
| `pipeline.py` | The stages after `stop`, resumably. |
| `state.py` | One JSON file, written atomically. The job's entire memory. |
| `tui.py` | The app, and the creature. |

## The two recorders

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

**Mixing happens per segment, not per track.** The two recorders do not start on the
same millisecond. Mixing `mic.wav` against `sys.wav` as whole tracks would hold that
offset for the length of the meeting; mixing `mic-03.wav` against `sys-03.wav` and
then concatenating keeps each error inside its own segment. The mix is
`amix=duration=longest:normalize=0` — without `normalize=0`, ffmpeg halves the level
of both inputs — followed by `dynaudnorm`, because a voice through the speakers and
a voice in the room rarely arrive at the same loudness.

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
| use the API instead of your subscription | export `ANTHROPIC_API_KEY`, set `backend = "api"` |

After editing anything under `mispy/` in a clone, re-run `./install.sh`.

## Known limits

- **No speaker diarisation.** Voices are not separated. Attribution comes from
  names being spoken, and is left blank otherwise, on purpose.
- **Recording stops when the lid closes.** Clamshell sleep is enforced by the
  firmware on Apple Silicon; `caffeinate` prevents *idle* sleep, not that.
- **Attachments are read as text.** A deck's diagrams do not reach the model.
- **One meeting at a time.**
- **The minutes are only as good as the transcript**, which is why the glossary is
  the highest-leverage file in the whole project.
