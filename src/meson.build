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

melange_sources = [
  '__init__.py',
  'main.py',
  'projectm.py',
  'webview.py',
  'window.py',
  'style.css'
]

install_data(melange_sources, install_dir: moduledir)

install_subdir(
  'web',
  install_dir: moduledir
)
