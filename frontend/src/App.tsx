import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

type Line = { speaker: number | null; text: string }
type Tip = { kind: string; term: string; note: string }
type Mark = { id: number; topic: string; quote: string; kind?: string; done?: boolean }

// Wrap flagged terms in <mark>, case-insensitive.
function highlight(text: string, terms: string[]) {
  if (!terms.length) return text
  const escaped = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  return text.split(new RegExp(`(${escaped.join('|')})`, 'gi')).map((part, i) =>
    i % 2 ? <mark key={i} className="bg-amber-200/60 px-0.5 text-inherit">{part}</mark> : part)
}

export default function App() {
  const [mics, setMics] = useState<MediaDeviceInfo[]>([])
  const [micId, setMicId] = useState(() => localStorage.getItem('micId') ?? '')
  const [card, setCard] = useState<{ label: string; value: string; verdict: string; why: string }[]>([])
  const [feed, setFeed] = useState(() => localStorage.getItem('feed') ?? '')
  const [notes, setNotes] = useState<{ notes: string; chapters: { at: string; title: string }[]; titles: string[]; styled_on_feed: boolean } | null>(null)
  const [running, setRunning] = useState(false)
  const [level, setLevel] = useState(0)
  const [lines, setLines] = useState<Line[]>([])
  const [interim, setInterim] = useState('')
  const [status, setStatus] = useState('')
  const [tips, setTips] = useState<Tip[]>([])
  const [marks, setMarks] = useState<Mark[]>([])
  const [nudge, setNudge] = useState<string | null>(null)
  const [audio, setAudio] = useState<'ok' | 'clipping' | 'silent'>('ok')
  const [seconds, setSeconds] = useState(0)
  // Rundown: one segment per line, "name minutes" (e.g. "Guest story 15"). Plain timers, no AI.
  const [rundown, setRundown] = useState(() => localStorage.getItem('rundown') ?? '')
  const [seg, setSeg] = useState({ i: 0, from: 0 })
  const segments = rundown.split('\n').map(l => l.match(/^(.*\S)\s+(\d+)\s*$/)).filter(m => m).map(m => ({ name: m![1], min: +m![2] }))
  const stopRef = useRef<() => void>(() => {})
  const markRef = useRef<() => void>(() => {})
  const [moments, setMoments] = useState<{ at: string; kind: string; note: string }[]>([])

  // M marks a moment for the edit list (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'm' && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) markRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = transcriptRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, interim])

  // Talk-time share by words spoken, per speaker.
  const words = new Map<number | null, number>()
  lines.forEach(l => words.set(l.speaker, (words.get(l.speaker) ?? 0) + l.text.split(' ').length))
  const totalWords = [...words.values()].reduce((a, b) => a + b, 0)

  useEffect(() => {
    // Labels only show after mic permission, so ask once up front.
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(s => s.getTracks().forEach(t => t.stop()))
      .then(() => navigator.mediaDevices.enumerateDevices())
      .then(d => setMics(d.filter(x => x.kind === 'audioinput')))
      .catch(e => setStatus(`Mic access failed: ${e.message}`))
  }, [])

  async function start() {
    localStorage.setItem('micId', micId)
    // Demo: play a recorded episode through the same pipeline instead of a mic (also audible on the speakers).
    const [demoFile, lang = 'he'] = micId.startsWith('demo:') ? micId.slice(5).split('|') : ['']
    const demo = demoFile ? new Audio(demoFile) : null
    const stream = demo ? null : await navigator.mediaDevices.getUserMedia({ audio: micId ? { deviceId: { exact: micId } } : true })

    // 16 kHz context: the browser resamples, and the server gets the exact format Deepgram and the pitch tracker expect.
    const ctx = new AudioContext({ sampleRate: 16000 })
    const source = demo ? ctx.createMediaElementSource(demo) : ctx.createMediaStreamSource(stream!)
    if (demo) { source.connect(ctx.destination); demo.play() }
    // Volume bar: RMS of the waveform, ~60 times a second.
    const analyser = ctx.createAnalyser()
    source.connect(analyser)
    const buf = new Float32Array(analyser.fftSize)
    let raf = 0
    let lastSound = Date.now()
    let lastClip = 0
    // shortcut: fixed thresholds, add a per-mic calibration step if they misfire on real gear
    const tick = () => {
      analyser.getFloatTimeDomainData(buf)
      const rms = Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length)
      const now = Date.now()
      if (rms > 0.01) lastSound = now
      if (buf.some(v => Math.abs(v) > 0.99)) lastClip = now
      setLevel(Math.min(1, rms * 4))
      setAudio(now - lastClip < 2000 ? 'clipping' : now - lastSound > 8000 ? 'silent' : 'ok')
      raf = requestAnimationFrame(tick)
    }
    tick()
    const startedAt = Date.now()
    setSeconds(0)
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000)

    localStorage.setItem('feed', feed)
    const ws = new WebSocket(`ws://${location.host}/ws/transcribe?feed=${encodeURIComponent(feed)}&lang=${lang}`)
    markRef.current = () => ws.readyState === WebSocket.OPEN && ws.send('mark')
    // Raw PCM capture: a tiny worklet hands us float samples, we send 16-bit chunks every ~250 ms.
    const worklet = `registerProcessor('pcm', class extends AudioWorkletProcessor {
      process(inputs) { if (inputs[0][0]) this.port.postMessage(inputs[0][0].slice(0)); return true }
    })`
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([worklet], { type: 'application/javascript' })))
    const pcmNode = new AudioWorkletNode(ctx, 'pcm')
    source.connect(pcmNode).connect(ctx.destination) // outputs silence; connected so it keeps running
    let pending: Float32Array[] = []
    const flush = () => {
      const n = pending.reduce((a, c) => a + c.length, 0)
      const out = new Int16Array(n)
      let i = 0
      for (const c of pending) for (const v of c) out[i++] = Math.max(-1, Math.min(1, v)) * 32767
      pending = []
      if (ws.readyState === WebSocket.OPEN) ws.send(out.buffer)
    }
    pcmNode.port.onmessage = e => {
      pending.push(e.data)
      if (pending.length >= 32) flush() // 32 x 128 samples = 256 ms
    }
    let saved = false
    ws.onmessage = e => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'saved') { saved = true; return setStatus(`Saved: ${msg.path}`) }
      if (msg.type === 'notes') return setNotes(msg)
      if (msg.type === 'scorecard') return setCard(msg.rows)
      if (msg.type === 'tip') return setTips(t => [...t, msg])
      if (msg.type === 'mark') return setMarks(m => [...m, msg])
      if (msg.type === 'nudge') return setNudge(msg.text)
      if (msg.type === 'moment') return setMoments(m => [...m, msg])
      if (msg.type === 'resolved') return setMarks(m => m.map(x => x.id === msg.id ? { ...x, done: true } : x))
      if (msg.is_final) {
        // Same speaker keeps talking: extend their paragraph instead of starting a new one.
        setLines(l => l.at(-1)?.speaker === msg.speaker
          ? [...l.slice(0, -1), { speaker: msg.speaker, text: `${l.at(-1)!.text} ${msg.text}` }]
          : [...l, { speaker: msg.speaker, text: msg.text }])
        setInterim('')
      } else setInterim(msg.text)
    }
    let stopped = false
    const release = () => {
      stopped = true
      pcmNode.port.onmessage = null
      stream?.getTracks().forEach(t => t.stop())
      demo?.pause()
      cancelAnimationFrame(raf)
      clearInterval(timer)
      ctx.close()
      setLevel(0)
      setAudio('ok')
      setRunning(false)
    }
    stopRef.current = () => {
      flush()
      if (ws.readyState === WebSocket.OPEN) ws.send('stop')
      else ws.close()
      setStatus('Writing show notes...')
      release()
    }
    // The server can end the session too (bad API key, dropped Deepgram connection): stop recording.
    ws.onclose = () => {
      if (stopped) return
      release()
      if (!saved) setStatus('Disconnected from the server')
    }
    setLines([])
    setTips([])
    setMarks([])
    setNudge(null)
    setMoments([])
    setNotes(null)
    setCard([])
    setSeg({ i: 0, from: 0 })
    localStorage.setItem('rundown', rundown)
    setStatus('Listening...')
    setRunning(true)
  }

  const clock = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
  const mmss = clock(seconds)
  const audioText = { ok: 'Audio OK', clipping: 'Too loud - clipping', silent: 'No sound for 8s - check the mic' }[audio]

  const micItems = [{ value: '', label: 'Default mic' }, ...mics.map(m => ({ value: m.deviceId, label: m.label || 'Unnamed mic' })),
    { value: 'demo:/demo.wav', label: 'Demo: Hebrew clip A (flat)' },
    { value: 'demo:/demo-good.wav', label: 'Demo: Hebrew clip B (good)' },
    { value: 'demo:/demo-flat-2.wav', label: 'Demo: Hebrew clip C (flat)' },
    { value: 'demo:/demo-en-good.wav|en', label: 'Demo: English clip A (good)' },
    { value: 'demo:/demo-en-bad.wav|en', label: 'Demo: English clip B (flat)' }]
  const label = 'text-xs font-semibold uppercase tracking-wider text-muted-foreground'

  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b px-6 py-4 md:px-12">
        <h1 className="mr-auto font-serif text-2xl font-semibold">Podcast Director</h1>
        <input value={feed} onChange={e => setFeed(e.target.value)} disabled={running} dir="ltr"
          placeholder="Your show's RSS (optional)" aria-label="Your show's RSS feed, to match your show notes style"
          className="h-11 w-56 border border-foreground bg-transparent px-3 text-sm" />
        <Select items={micItems} value={micId} onValueChange={v => setMicId(v ?? '')} disabled={running}>
          <SelectTrigger className="h-11 w-64 border-foreground bg-transparent" aria-label="Microphone"><SelectValue /></SelectTrigger>
          <SelectContent className="min-w-80">
            {micItems.map(m => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Progress value={level * 100} className="w-32" aria-label="Mic level" />
        <span className={`flex items-center gap-2 tabular-nums ${running ? '' : 'text-muted-foreground'}`}>
          <span className={`size-2 rounded-full ${running ? 'animate-pulse bg-destructive' : 'bg-muted-foreground'}`} />
          {mmss}
        </span>
        {running && <Button size="lg" variant="outline" className="h-11" onClick={() => markRef.current()}>Mark (M)</Button>}
        <Button size="lg" className="h-11 min-w-24" onClick={running ? () => stopRef.current() : () => start().catch(e => setStatus(`Could not start: ${e.message}`))}>
          {running ? 'Stop' : 'Start'}
        </Button>
      </header>

      {card.length > 0 && (
        <section className="mx-auto max-w-6xl border-b px-6 py-8 md:px-12">
          <h2 className="mb-4 font-serif text-2xl font-semibold">Was it a good talk?</h2>
          <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
            {card.map(r => (
              <div key={r.label} className="border-t pt-2" title={r.why}>
                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{r.label}</span>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-serif text-xl tabular-nums">{r.value}</span>
                  <span className={`text-xs font-semibold uppercase ${r.verdict === 'work' ? 'text-destructive' : r.verdict === 'good' ? '' : 'text-muted-foreground'}`}>
                    {{ good: 'Good', ok: 'OK', work: 'Work on it' }[r.verdict]}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">{r.why}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {notes && (
        <section className="mx-auto max-w-6xl border-b px-6 py-8 md:px-12">
          <h2 className="mb-1 font-serif text-2xl font-semibold">Show notes</h2>
          <p className="mb-4 text-sm text-muted-foreground">{notes.styled_on_feed ? 'Written in the style of your previous episodes.' : 'Add your RSS feed to match your own style.'}</p>
          <div className="grid gap-8 md:grid-cols-[minmax(0,1fr)_300px]">
            <p dir="rtl" className="whitespace-pre-wrap font-serif text-lg leading-relaxed">{notes.notes}</p>
            <div dir="rtl" className="grid content-start gap-4 text-sm">
              <div><b className="block">פרקים</b>{notes.chapters.map((c, i) => <div key={i} className="tabular-nums">{c.at} {c.title}</div>)}</div>
              <div><b className="block">רעיונות לשם</b>{notes.titles.map((t, i) => <div key={i}>{t}</div>)}</div>
            </div>
          </div>
        </section>
      )}

      <main className="mx-auto grid max-w-6xl gap-14 px-6 py-10 md:grid-cols-[minmax(0,1fr)_300px] md:px-12">
        <article ref={transcriptRef} aria-live="polite" className="max-h-[calc(100vh-170px)] overflow-y-auto font-serif text-xl leading-relaxed">
          {!lines.length && !interim && <p className="text-muted-foreground">{status || 'Press Start and talk. Your words show up here.'}</p>}
          {lines.map((l, i) => (
            <p className="mb-6" key={i} dir="auto">
              <span className={`${label} block font-sans`}>Speaker {l.speaker ?? '?'}</span>
              {highlight(l.text, tips.filter(t => t.kind === 'term').map(t => t.term))}
              {i === lines.length - 1 && interim && <span className="text-muted-foreground"> {interim}</span>}
            </p>
          ))}
          {!lines.length && interim && <p className="mb-6 text-muted-foreground">{interim}</p>}
        </article>

        <aside className="grid content-start gap-7 border-l pl-7 text-sm">
          {segments.length > 0 && (running ? (() => {
            const cur = segments[seg.i], used = seconds - seg.from, over = used - cur.min * 60
            return (
              <section>
                <h2 className={`${label} mb-2`}>Segment {seg.i + 1} of {segments.length}</h2>
                <p className="font-serif text-lg">{cur.name}</p>
                <p className={`tabular-nums ${over > 0 ? 'font-semibold text-destructive' : 'text-muted-foreground'}`}>
                  {clock(Math.max(0, used))} / {cur.min}:00{over > 0 && ` · ${clock(over)} over, time to wrap this up`}
                </p>
                {seg.i < segments.length - 1 && (
                  <Button variant="outline" className="mt-2 h-9" onClick={() => setSeg({ i: seg.i + 1, from: seconds })}>
                    Next: {segments[seg.i + 1].name}
                  </Button>
                )}
              </section>
            )
          })() : null)}
          {!running && (
            <section>
              <h2 className={`${label} mb-2`}>Rundown (optional)</h2>
              <textarea value={rundown} onChange={e => setRundown(e.target.value)} rows={4} dir="auto"
                placeholder={'Intro 3\nGuest story 15\nWrap up 5'} aria-label="Rundown: one segment per line, name and minutes"
                className="w-full border border-input bg-transparent p-2 text-sm" />
            </section>
          )}
          {nudge && (
            <p role="status" className="border-l-2 border-destructive bg-secondary px-3 py-2 font-serif text-lg leading-snug">{nudge}</p>
          )}
          <section>
            <h2 className={`${label} mb-2`}>Audio</h2>
            <p role="status" className={running && audio !== 'ok' ? 'font-semibold text-destructive' : 'text-muted-foreground'}>
              {running ? audioText : 'Not recording'}
            </p>
          </section>
          <section>
            <h2 className={`${label} mb-2`}>Talk time</h2>
            {totalWords ? [...words].map(([speaker, n]) => (
              <div className="mb-2 grid grid-cols-[72px_1fr_36px] items-center gap-2" key={String(speaker)}>
                <span>Speaker {speaker ?? '?'}</span>
                <Progress value={(n / totalWords) * 100} aria-label={`Speaker ${speaker} share`} />
                <span className="tabular-nums">{Math.round((n / totalWords) * 100)}%</span>
              </div>
            )) : <p className="text-muted-foreground">Shows who's talking how much.</p>}
          </section>
          <section>
            <h2 className={`${label} mb-2`}>Come back to</h2>
            {marks.length ? marks.map(m => (
              <p className={`mb-4 ${m.done ? 'text-muted-foreground line-through' : ''}`} key={m.id}>
                <b className="block font-semibold">{m.topic}{m.kind && m.kind !== 'postponed' && <span className="font-normal text-muted-foreground"> · {m.kind === 'hanging' ? 'left hanging' : 'their point'}</span>}</b>
                <span className="font-serif italic">"{m.quote}"</span>
              </p>
            )) : <p className="text-muted-foreground">Topics you put off ("we'll get back to that") show up here.</p>}
          </section>
          <section>
            <h2 className={`${label} mb-2`}>Edit list</h2>
            {moments.length ? moments.map((m, i) => (
              <p className="mb-1 tabular-nums" key={i}><b className="font-semibold">{m.at}</b> · {m.kind} · <span className="text-muted-foreground">{m.note}</span></p>
            )) : <p className="text-muted-foreground">Clips, retakes and sponsor reads get marked here. Press M to mark one yourself.</p>}
          </section>
          <section>
            <h2 className={`${label} mb-2`}>Margin notes</h2>
            {tips.length ? [...tips].reverse().map((t, i) => (
              <p className={`mb-4 ${t.kind !== 'term' ? 'border-l-2 border-foreground pl-2' : ''}`} key={i}><b className="block font-semibold">{t.term}</b>{t.note}</p>
            )) : <p className="text-muted-foreground">Words listeners may not know show up here.</p>}
          </section>
          {status && lines.length > 0 && <p className="text-muted-foreground">{status}</p>}
        </aside>
      </main>
    </div>
  )
}
