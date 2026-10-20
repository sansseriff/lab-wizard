from lab_wizard.lib.instruments.general.parent_child import ChannelProvider

# Simple structural test for the ChannelProvider mixin. Avoids real hardware deps.


def test_channelchild_interface_presence():
    # This is a structural test: we don't instantiate real hardware modules here
    # because their creation may require backend servers. Instead, verify the mixin
    # provides the expected methods.
    class Dummy(ChannelProvider[int]):  # type: ignore[type-arg]
        def __init__(self):
            self.channels = [1, 2, 3]

    d = Dummy()
    assert d.num_channels == 3
    assert d.get_channel(1) == 2
    assert list(iter(d)) == [1, 2, 3]
    assert d[2] == 3
