// One command, two servers: FastAPI (8000) + Vite dev server with hot reload (5173).
import { spawn } from 'node:child_process'
import { watch } from 'node:fs'

const run = (cmd, args, opts = {}) => spawn(cmd, args, { stdio: 'inherit', ...opts })

// uvicorn --reload hangs on this Windows setup, so restart the backend ourselves on .py changes.
// python -m uvicorn, not uvicorn.exe: the .exe is a launcher, and killing it leaves the real server running old code.
const python = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'
const backend = () => run(python, ['-m', 'uvicorn', 'backend.main:app', '--port', '8000'])
let api = backend()
let timer
watch('backend', (_, file) => {
  if (!file?.endsWith('.py')) return
  clearTimeout(timer)
  timer = setTimeout(() => {
    if (!api) return // a restart is already on its way and will load the latest code
    console.log(`\n${file} changed, restarting backend...`)
    const old = api
    api = null
    if (old.exitCode !== null || old.signalCode !== null) api = backend() // already gone (e.g. crashed)
    else { old.once('exit', () => { api = backend() }); old.kill() }
  }, 300)
})

const web = run('npm', ['run', 'dev'], { cwd: 'frontend', shell: true })
process.on('SIGINT', () => { api?.kill(); web.kill(); process.exit() })
