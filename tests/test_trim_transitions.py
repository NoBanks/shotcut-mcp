"""Trim edits preserve existing crossfade constraints without changing state on error."""
import copy
import unittest
import xml.etree.ElementTree as ET
from shotcut_mcp.mlt_xml import MltProject

class TrimTransitionTests(unittest.TestCase):
    def project(self):
        p = MltProject('trim-crossfade', fps=25)
        ids = [p.add_clip(f'{n}.mp4', in_s=0, out_s=10) for n in range(3)]
        return p, ids

    def test_trim_rejects_shortening_either_side_and_preserves_all_state(self):
        for side in (0, 1):
            for trim in ({'out_s': .5}, {'in_s': 7}, {'in_s': 2, 'out_s': 5}):
                with self.subTest(side=side, trim=trim):
                    p, ids = self.project()
                    p.set_transition(ids[0], ids[1], 2)
                    before = copy.deepcopy(p.__dict__)
                    with self.assertRaisesRegex(ValueError, 'existing.*crossfade'):
                        p.trim_clip(ids[side], **trim)
                    self.assertEqual(p.__dict__, before)

    def test_middle_clip_checks_both_crossfades(self):
        p, ids = self.project()
        p.set_transition(ids[0], ids[1], 1)
        p.set_transition(ids[1], ids[2], 2)
        before = copy.deepcopy(p.__dict__)
        with self.assertRaises(ValueError):
            p.trim_clip(ids[1], out_s=3)
        self.assertEqual(p.__dict__, before)

    def test_boundary_trim_preserves_source_bounds_and_playlist_duration(self):
        p, ids = self.project()
        p.set_transition(ids[0], ids[1], 2)
        p.set_transition(ids[1], ids[2], 2)
        p.trim_clip(ids[1], in_s=3, out_s=7)
        xml = ET.fromstring(p.to_xml())
        entries = xml.find("playlist[@id='playlist0']").findall('entry')
        frames = sum(int(e.get('out')) - int(e.get('in')) + 1 for e in entries)
        self.assertEqual(frames, round(p.project_duration_s() * p.fps))
        for track in xml.findall('tractor/track'):
            if track.get('producer') == ids[1]:
                self.assertGreaterEqual(int(track.get('in')), 75)
                self.assertLess(int(track.get('out')), 175)

    def test_unconnected_clip_can_still_be_trimmed_short(self):
        p, ids = self.project()
        p.set_transition(ids[0], ids[1], 2)
        p.trim_clip(ids[2], out_s=.5)
        self.assertEqual(p.timeline()['clips'][2]['out_s'], .5)

    def test_noop_and_extension_preserve_transition(self):
        p, ids = self.project()
        p.set_transition(ids[0], ids[1], 2)
        transitions = copy.deepcopy(p.timeline()['transitions'])
        p.trim_clip(ids[0])
        p.trim_clip(ids[0], out_s=12)
        self.assertEqual(p.timeline()['transitions'], transitions)

if __name__ == '__main__':
    unittest.main()
