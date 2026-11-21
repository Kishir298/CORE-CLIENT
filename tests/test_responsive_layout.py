import pytest

# The desktop layout module imports tkinter; skip on hosts whose Python was
# built without Tk instead of aborting collection for the whole suite.
pytest.importorskip("tkinter")

from client.desktop.layout import flow_positions, initial_geometry, wrap_text


@pytest.mark.parametrize('width,height',[(320,240),(480,800),(800,480),(1280,720),(3840,2160)])
def test_initial_window_fits_small_and_large_displays(width,height):
    window=initial_geometry(width,height)
    assert 0<window[0]<width and 0<window[1]<height

@pytest.mark.parametrize('width',[160,240,320,800,1600])
def test_controls_wrap_without_horizontal_overflow(width):
    sizes=[min(width,x) for x in [120,220,80,180,260]]
    positions=flow_positions(width,sizes)
    for (_,x),size in zip(positions,sizes):assert 0<=x and x+size<=width
    if width==160:assert positions[-1][0]>0


def test_long_button_label_wraps_without_losing_words():
    text='Download recommended local model'
    wrapped=wrap_text(text,12,len)
    assert ' '.join(wrapped.split())==text
    assert all(len(line)<=12 for line in wrapped.splitlines())
