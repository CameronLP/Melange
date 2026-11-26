pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'melange'
gnome = import('gnome')

gnome.compile_resources('melange',
  'melange.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'melange.in',
  output: 'melange',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

git = find_program('git', required: false)
if git.found()
  r = run_command(git, 'rev-parse', '--short', 'HEAD', check: false)
  commit = r.returncode() == 0 ? r.stdout().strip() : 'unknown'
else
  commit = 'unknown'
endif

py3 = python.find_installation('python3')
r = run_command(py3, '-c',
  'import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))',
  check: false)
build_time = r.returncode() == 0 ? r.stdout().strip() : 'unknown'

version_conf = configuration_data()
version_conf.set('COMMIT', commit)
version_conf.set('BUILD_TIME', build_time)

configure_file(
  input: '_version.py.in',
  output: '_version.py',
  configuration: version_conf,
  install: true,
  install_dir: moduledir
)

melange_sources = [
  '__init__.py',
  'main.py',
  'mini_visualizer_base.py',
  'aux_window.py',
  'cursor_hider.py',
  'mirror_window.py',
  'now_playing.py',
  'preset_manager.py',
  'projectm_view.py',
  'window.py',
  'style.css'
]

install_data(melange_sources, install_dir: moduledir)
