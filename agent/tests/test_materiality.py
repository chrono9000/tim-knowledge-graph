import unittest
from agent.materiality import classify_materiality
from agent.extraction import Message,DeterministicExtractor,extract_document

class MaterialityTests(unittest.TestCase):
    def test_microphone_context(self):
        self.assertFalse(classify_materiality('The microphone checks are for this recording project, and Lucia Chen is waiting for them.')['standalone'])
    def test_varied_temporary_context(self):
        for text in ['The parcel just arrived at reception.','I acknowledged the note.','Mira stepped out for coffee.','The sketches are on my desk.','The visitor waited in the lobby.']:
            self.assertFalse(classify_materiality(text)['standalone'],text)
    def test_waiting_with_material_consequence(self):
        for text in ['Waiting for the permit will delay the launch.','The team is waiting for the safety checks; this blocks release.','A missing test report puts the deadline at risk.']:
            self.assertTrue(classify_materiality(text)['standalone'],text)
            self.assertEqual('high',classify_materiality(text)['priority'])
    def test_material_categories_retained(self):
        for category in ['decision','commitment','risk','constraint','preference','responsibility','superseded']:
            self.assertTrue(classify_materiality('Explicit operational knowledge.',category)['standalone'])
    def test_context_retained_without_graph_node(self):
        ms=(Message('m1','user','Lucia is waiting for the microphone checks. Alex decided to launch Project Aurora.','2027-01-01T00:00:00Z'),)
        doc,evidence=extract_document(ms,DeterministicExtractor())
        self.assertEqual(1,len(doc.supporting_context))
        self.assertFalse(any('waiting' in n.description for n in doc.nodes.values()))
        self.assertTrue(any('launch' in n.description for n in doc.nodes.values()))
