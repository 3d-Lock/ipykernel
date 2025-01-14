"""A matplotlib backend for publishing figures via display_data"""

# Copyright (c) IPython Development Team.
# Distributed under the terms of the Modified BSD License.

import matplotlib
from matplotlib.backends.backend_agg import (
    new_figure_manager,
    FigureCanvasAgg,
    new_figure_manager_given_figure,
) # analysis: ignore
from matplotlib import colors
from matplotlib._pylab_helpers import Gcf

from IPython.core.getipython import get_ipython
from IPython.core.pylabtools import select_figure_formats
from IPython.display import display

from .config import InlineBackend


def show(close=None, block=None):
    """Show all figures as SVG/PNG payloads sent to the IPython clients.

    Parameters
    ----------
    close : bool, optional
        If true, a ``plt.close('all')`` call is automatically issued after
        sending all the figures. If this is set, the figures will entirely
        removed from the internal list of figures.
    block : Not used.
        The `block` parameter is a Matplotlib experimental parameter.
        We accept it in the function signature for compatibility with other
        backends.
    """
    if close is None:
        close = InlineBackend.instance().close_figures
    try:
        for figure_manager in Gcf.get_all_fig_managers():
            display(
                figure_manager.canvas.figure,
                metadata=_fetch_figure_metadata(figure_manager.canvas.figure)
            )
    finally:
        show._to_draw = []
        # only call close('all') if any to close
        # close triggers gc.collect, which can be slow
        if close and Gcf.get_all_fig_managers():
            matplotlib.pyplot.close('all')


# This flag will be reset by draw_if_interactive when called
show._draw_called = False
# list of figures to draw when flush_figures is called
show._to_draw = []


def draw_if_interactive():
    """
    Is called after every pylab drawing command
    """
    # signal that the current active figure should be sent at the end of
    # execution.  Also sets the _draw_called flag, signaling that there will be
    # something to send.  At the end of the code execution, a separate call to
    # flush_figures() will act upon these values
    manager = Gcf.get_active()
    if manager is None:
        return
    fig = manager.canvas.figure

    # Hack: matplotlib FigureManager objects in interacive backends (at least
    # in some of them) monkeypatch the figure object and add a .show() method
    # to it.  This applies the same monkeypatch in order to support user code
    # that might expect `.show()` to be part of the official API of figure
    # objects.
    # For further reference:
    # https://github.com/ipython/ipython/issues/1612
    # https://github.com/matplotlib/matplotlib/issues/835

    if not hasattr(fig, 'show'):
        # Queue up `fig` for display
        fig.show = lambda *a: display(fig, metadata=_fetch_figure_metadata(fig))

    # If matplotlib was manually set to non-interactive mode, this function
    # should be a no-op (otherwise we'll generate duplicate plots, since a user
    # who set ioff() manually expects to make separate draw/show calls).
    if not matplotlib.is_interactive():
        return

    # ensure current figure will be drawn, and each subsequent call
    # of draw_if_interactive() moves the active figure to ensure it is
    # drawn last
    try:
        show._to_draw.remove(fig)
    except ValueError:
        # ensure it only appears in the draw list once
        pass
    # Queue up the figure for drawing in next show() call
    show._to_draw.append(fig)
    show._draw_called = True


def flush_figures():
    """Send all figures that changed

    This is meant to be called automatically and will call show() if, during
    prior code execution, there had been any calls to draw_if_interactive.

    This function is meant to be used as a post_execute callback in IPython,
    so user-caused errors are handled with showtraceback() instead of being
    allowed to raise.  If this function is not called from within IPython,
    then these exceptions will raise.
    """
    if not show._draw_called:
        return

    if InlineBackend.instance().close_figures:
        # ignore the tracking, just draw and close all figures
        try:
            return show(True)
        except Exception as e:
            # safely show traceback if in IPython, else raise
            ip = get_ipython()
            if ip is None:
                raise e
            else:
                ip.showtraceback()
                return
    try:
        # exclude any figures that were closed:
        active = set([fm.canvas.figure for fm in Gcf.get_all_fig_managers()])
        for fig in [ fig for fig in show._to_draw if fig in active ]:
            try:
                display(fig, metadata=_fetch_figure_metadata(fig))
            except Exception as e:
                # safely show traceback if in IPython, else raise
                ip = get_ipython()
                if ip is None:
                    raise e
                else:
                    ip.showtraceback()
                    return
    finally:
        # clear flags for next round
        show._to_draw = []
        show._draw_called = False


# Changes to matplotlib in version 1.2 requires a mpl backend to supply a default
# figurecanvas. This is set here to a Agg canvas
# See https://github.com/matplotlib/matplotlib/pull/1125
FigureCanvas = FigureCanvasAgg


def configure_inline_support(shell, backend):
    """Configure an IPython shell object for matplotlib use.

    Parameters
    ----------
    shell : InteractiveShell instance
    backend : matplotlib backend
    """
    # If using our svg payload backend, register the post-execution
    # function that will pick up the results for display.  This can only be
    # done with access to the real shell object.

    cfg = InlineBackend.instance(parent=shell)
    cfg.shell = shell
    if cfg not in shell.configurables:
        shell.configurables.append(cfg)

    if backend == 'module://ipykernel.pylab.backend_inline':
        shell.events.register('post_execute', flush_figures)

        # Save rcParams that will be overwrittern
        shell._saved_rcParams = {}
        for k in cfg.rc:
            shell._saved_rcParams[k] = matplotlib.rcParams[k]
        # load inline_rc
        matplotlib.rcParams.update(cfg.rc)
        new_backend_name = "inline"
    else:
        try:
            shell.events.unregister('post_execute', flush_figures)
        except ValueError:
            pass
        if hasattr(shell, '_saved_rcParams'):
            matplotlib.rcParams.update(shell._saved_rcParams)
            del shell._saved_rcParams
        new_backend_name = "other"

    # only enable the formats once -> don't change the enabled formats (which the user may
    # has changed) when getting another "%matplotlib inline" call.
    # See https://github.com/ipython/ipykernel/issues/29
    cur_backend = getattr(configure_inline_support, "current_backend", "unset")
    if new_backend_name != cur_backend:
        # Setup the default figure format
        select_figure_formats(shell, cfg.figure_formats, **cfg.print_figure_kwargs)
        configure_inline_support.current_backend = new_backend_name


def _enable_matplotlib_integration():
    """Enable extra IPython matplotlib integration when we are loaded as the matplotlib backend."""
    from matplotlib import get_backend
    ip = get_ipython()
    backend = get_backend()
    if ip and backend == 'module://%s' % __name__:
        from IPython.core.pylabtools import activate_matplotlib
        try:
            activate_matplotlib(backend)
            configure_inline_support(ip, backend)
        except (ImportError, AttributeError):
            # bugs may cause a circular import on Python 2
            def configure_once(*args):
                activate_matplotlib(backend)
                configure_inline_support(ip, backend)
                ip.events.unregister('post_run_cell', configure_once)
            ip.events.register('post_run_cell', configure_once)

_enable_matplotlib_integration()

def _fetch_figure_metadata(fig):
    """Get some metadata to help with displaying a figure."""
    # determine if a background is needed for legibility
    if _is_transparent(fig.get_facecolor()):
        # the background is transparent
        ticksLight = _is_light([label.get_color()
                                for axes in fig.axes
                                for axis in (axes.xaxis, axes.yaxis)
                                for label in axis.get_ticklabels()])
        if ticksLight.size and (ticksLight == ticksLight[0]).all():
            # there are one or more tick labels, all with the same lightness
            return {'needs_background': 'dark' if ticksLight[0] else 'light'}

    return None

def _is_light(color):
    """Determines if a color (or each of a sequence of colors) is light (as
    opposed to dark). Based on ITU BT.601 luminance formula (see
    https://stackoverflow.com/a/596241)."""
    rgbaArr = colors.to_rgba_array(color)
    return rgbaArr[:,:3].dot((.299, .587, .114)) > .5

def _is_transparent(color):
    """Determine transparency from alpha."""
    rgba = colors.to_rgba(color)
    return rgba[3] < .5
