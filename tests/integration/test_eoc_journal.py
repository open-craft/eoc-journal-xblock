"""
Basic integration tests for the EOC Journal XBlock.
"""

import json
import unittest

from django.test import override_settings
from django.test.client import Client
from mock import MagicMock, Mock, patch
from reportlab.lib.styles import StyleSheet1
from reportlab.rl_config import canvas_basefontname
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import Select
from xblockutils.resources import ResourceLoader
from xblockutils.studio_editable_test import StudioEditableBaseTest

from eoc_journal.pdf_generator import get_style_sheet
from .utils import extract_text_from_pdf

loader = ResourceLoader(__name__)


def checkbox_value(block_id):
    """
    Checkbox value attributes are wrapped in double quotes.
    This method wraps a block_id (string) into double quotes,
    which is useful in tests to find checkboxes by block id
    and comparing checkbox values to expected block ids.
    """
    return '"{}"'.format(block_id)


class FakeQuerySet(object):
    def __init__(self, objs):
        self.objects = objs

    def __iter__(self):
        for o in self.objects:
            yield o

    def count(self):
        return len(self.objects)


class FakeAnswer(object):
    def __init__(self, name, student_input, question):
        self.name = name
        self.student_input = student_input
        self.question = question


default_pb_answer_block_ids = [
    'i4x://Org/Course/pb-answer/b86edf60454b47dbb8f2e1b4e2d48d6a',
    'i4x://Org/Course/pb-answer/6f070c350e39429cbccfd3185a33621c',
    'i4x://Org/Course/pb-answer/a0b04a13d3074229b6be33fbc31de233',
    'i4x://Org/Course/pb-answer/927c5b8cd051475e937b8c1091a9feaf',
]

course_name = 'Testing Ways'

default_answers_data = [
    FakeAnswer("efac891", 'student input', '<p>Tell us more about <strong>yourself</strong>.</p>'),
]

# Note that questions may originally contain HTML tags, but HTML tags should be stripped
# when listing the questions/answers in the EOC journal.
expected_answers_data = [
    {'question': u'Hello, who are you? What\u2019s your name?', 'answer': 'Not answered yet.'},
    {'question': u'Tell us more about yourself.', 'answer': 'student input'},
    {'question': u'', 'answer': 'Not answered yet.'},
]

default_engagement_metrics = {
    'user_score': 6,
    'cohort_score': 9,
    'new_posts': 1,
    'total_replies': 2,
    'upvotes': 1,
    'comments_generated': 1,
    'posts_followed': 1,
}

default_completion_leader_metrics = {
    'position': 1,
    'course_avg': 17.5,
    'completions': 33.33,
}

default_user_progress = 4
default_cohort_average_progress = int(round(default_completion_leader_metrics['course_avg']))

default_user_proficiency = 83
default_cohort_average_proficiency = 44

@override_settings(ENV_TOKENS={'LMS_BASE': 'lms.base'}, HTTPS='on')
class TestEOCJournal(StudioEditableBaseTest):
    default_css_selector = 'div.oec-journal-block'
    module_name = __name__

    def setUp(self):
        super(TestEOCJournal, self).setUp()

        def mock_answers_filter(**kwargs):
            return FakeQuerySet(default_answers_data)

        answer_mock = MagicMock()
        answer_mock.objects.filter = mock_answers_filter
        self.patch('eoc_journal.eoc_journal.Answer', answer_mock)

        # Patch _get_current_user method.
        mock_user = Mock()
        mock_user.username = 'some-user'
        self.patch('eoc_journal.eoc_journal.EOCJournalXBlock._get_current_user', mock_user)
        self.patch(
            'eoc_journal.eoc_journal.EOCJournalXBlock._get_current_anonymous_user_id',
            lambda self: 'anonymous_user_id',
        )

        # Patch _get_course_name method.
        mock_course = Mock()
        mock_course.return_value = course_name
        self.patch('eoc_journal.eoc_journal.EOCJournalXBlock._get_course_name', mock_course)

        # Patch CourseBlocksApiClient.
        self.patch('eoc_journal.eoc_journal.CourseBlocksApiClient._connect', Mock())

        def mock_get_blocks(self, **kwargs):
            return json.loads(loader.load_unicode('data/course_api_response.json'))

        self.patch('eoc_journal.eoc_journal.CourseBlocksApiClient.get_blocks', mock_get_blocks)

        # Patch CompletionApiClient.
        self.patch('eoc_journal.eoc_journal.CompletionApiClient._connect', Mock())

        def mock_get_user_progress(self, **kwargs):
            return json.loads(loader.load_unicode('data/user_progress_response.json'))

        self.patch('eoc_journal.eoc_journal.CompletionApiClient.get_user_progress', mock_get_user_progress)

        # Patch UserMetricsClient.
        self.patch('eoc_journal.api_client.ApiClient._connect', Mock())

        def mock_get_user_engagement_metrics(self):
            return json.loads(loader.load_unicode('data/user_engagement_metrics_response.json'))

        self.patch(
            'eoc_journal.api_client.ApiClient.get_user_engagement_metrics',
            mock_get_user_engagement_metrics
        )

        def mock_get_grades_leader_metrics(self):
            return json.loads(loader.load_unicode('data/grades_leader_metrics_response.json'))

        self.patch(
            'eoc_journal.api_client.ApiClient._get_grades_leader_metrics',
            mock_get_grades_leader_metrics
        )

        def mock_get_course(self):
            return json.loads(loader.load_unicode('data/course_response.json'))

        self.patch(
            'eoc_journal.api_client.ApiClient._get_course',
            mock_get_course
        )

        def mock_get_completion_leader_metrics(self):
            return default_completion_leader_metrics

        self.patch(
            'eoc_journal.api_client.ApiClient._get_completion_leader_metrics',
            mock_get_completion_leader_metrics
        )

    def patch(self, item, return_value):
        patcher = patch(item, return_value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pdf_report_url(self):
        block = self.load_root_xblock()
        return block.runtime.handler_url(block, 'serve_pdf')

    def set_standard_scenario(self):
        scenario = '<eoc-journal url_name="defaults" />'
        self.set_scenario_xml(scenario)

    def get_element_for_pb_answers_field(self):
        field = self.browser.find_element_by_css_selector(
            'li.field[data-field-name=selected_pb_answer_blocks]'
        )
        return field

    def get_element_for_selected_pb_answers(self):
        element = self.browser.find_element_by_css_selector(
            'div.eoc-selected-answers'
        )
        return element

    def get_element_for_pb_answers_pdf_report(self):
        element = self.browser.find_element_by_css_selector(
            'div.eoc-pdf-report'
        )
        return element

    def get_element_for_course_progress(self):
        element = self.browser.find_element_by_css_selector(
            'div.progress-metrics'
        )
        return element

    def get_element_for_course_proficiency(self):
        element = self.browser.find_element_by_css_selector(
            'div.proficiency-metrics'
        )
        return element

    def get_element_for_course_engagement(self):
        element = self.browser.find_element_by_css_selector(
            'div.engagement-metrics'
        )
        return element

    def select_option_in_boolean_field(self, field_name, value=True):
        control = self.get_element_for_field(field_name)
        select = Select(control.find_element_by_id("xb-field-edit-{}".format(field_name)))
        selected_value = "1" if value else "0"
        select.select_by_value(selected_value)

    def configure_block(self,
                        display_name=None,
                        key_takeaways_pdf=None,
                        selected_pb_answer_blocks=None,
                        pdf_report_link_heading=None,
                        pdf_report_link_text=None,
                        display_metrics_section=None,
                        display_key_takeaways_section=None,
                        pdf_report_title=None,
                        display_answers=None
                        ):
        self.set_standard_scenario()
        self.go_to_view('studio_view')
        self.fix_js_environment()

        if display_name is not None:
            control = self.get_element_for_field('display_name')
            control.clear()
            control.send_keys(display_name)
        if key_takeaways_pdf is not None:
            control = self.get_element_for_field('key_takeaways_pdf')
            control.clear()
            control.send_keys(key_takeaways_pdf)
        if selected_pb_answer_blocks:
            field = self.get_element_for_pb_answers_field()
            for block_id in selected_pb_answer_blocks:
                selector = "input[type=checkbox][value='{}']".format(checkbox_value(block_id))
                control = field.find_element_by_css_selector(selector)
                control.click()
        if pdf_report_link_heading is not None:
            control = self.get_element_for_field('pdf_report_link_heading')
            control.clear()
            control.send_keys(pdf_report_link_heading)
        if pdf_report_link_text is not None:
            control = self.get_element_for_field('pdf_report_link_text')
            control.clear()
            control.send_keys(pdf_report_link_text)
        if pdf_report_title is not None:
            control = self.get_element_for_field('pdf_report_title')
            control.clear()
            control.send_keys(pdf_report_title)

        if display_metrics_section is not None:
            self.select_option_in_boolean_field(
                'display_metrics_section',
                display_metrics_section
            )

        if display_key_takeaways_section is not None:
            self.select_option_in_boolean_field(
                'display_key_takeaways_section',
                display_key_takeaways_section
            )

        if display_answers is not None:
            self.select_option_in_boolean_field(
                'display_answers',
                display_answers
            )

        self.click_save()

        self.element = self.go_to_view('student_view')

    def test_block_loads_when_apis_not_available(self):
        # Patch all engagement/progress/completion API related methods to return None.
        # These API related methods return None if the APIs are not available for some reason.
        # They are not available in the Studio, for example.
        api_client_methods = [
            'get_user_engagement_metrics',
            'get_cohort_average_progress',
            '_get_grades_leader_metrics',
            '_get_course',
        ]
        for method in api_client_methods:
            method_path = 'eoc_journal.api_client.ApiClient.{}'.format(method)
            self.patch(method_path, lambda self: None)

        self.configure_block(
            display_metrics_section=True
        )

        # Verify the student view does not break.
        self.assertIn('Progress data is not available.', self.element.text)
        self.assertIn('Proficiency data is not available.', self.element.text)
        self.assertIn('Engagement data is not available.', self.element.text)

    def test_pb_answer_blocks_listed_in_edit_view(self):
        self.set_standard_scenario()
        self.go_to_view('studio_view')
        self.fix_js_environment()

        field_control = self.get_element_for_pb_answers_field()
        items = field_control.find_elements_by_css_selector('.list-settings-item')
        # There are four pb-answer blocks in the mocked course blocks API response.
        self.assertEqual(len(items), 4)
        expected_choices = [
            {
                'value': default_pb_answer_block_ids[0],
                'title': 'First Section / Subsection 1 / Unit 3 / 23761f7',
            },
            {
                'value': default_pb_answer_block_ids[1],
                'title': 'Second Section / Subsection 2 / Unit 1 / Introductions',
            },
            {
                'value': default_pb_answer_block_ids[2],
                'title': 'Second Section / Subsection 2 / Unit 1 / More Info',
            },
            {
                'value': default_pb_answer_block_ids[3],
                'title': 'Second Section / Subsection 2 / Unit 1 / More Introductions',
            },
        ]
        for idx, item in enumerate(items):
            value = expected_choices[idx]['value']
            title = expected_choices[idx]['title']
            checkbox = item.find_element_by_css_selector('input[type=checkbox]')
            self.assertEqual(checkbox.get_attribute('value'), checkbox_value(value))
            label = item.find_element_by_css_selector('label')
            self.assertEqual(label.text, title)

    def test_pb_answers_hidden_in_student_view_by_default(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids)

        self.assertRaises(
            NoSuchElementException,
            self.get_element_for_selected_pb_answers
        )

    def test_pb_answers_listed_in_student_view_when_enabled(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(
            selected_pb_answer_blocks=selected_block_ids,
            display_answers=True
        )

        element = self.get_element_for_selected_pb_answers()

        items = element.find_elements_by_css_selector('.eoc-selected-answers-section')
        self.assertEqual(len(items), 1)

        section_name = items[0].find_element_by_css_selector('h4')
        self.assertEqual(section_name.text, 'Second Section')

        answers_in_section = items[0].find_elements_by_css_selector('.eoc-selected-answer')
        self.assertEqual(len(answers_in_section), 3)

        for element, expected in zip(answers_in_section, expected_answers_data):
            question = element.find_element_by_css_selector('h5')
            answer = element.find_element_by_css_selector('p')

            self.assertEqual(question.text, expected['question'])
            self.assertEqual(answer.text, expected['answer'])

    def test_link_to_pb_answers_pdf_displayed_in_student_view(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids)

        report_link_container = self.get_element_for_pb_answers_pdf_report()
        link = report_link_container.find_element_by_css_selector('a')

        self.assertTrue(link.get_attribute('href').endswith(self.pdf_report_url()))
        self.assert_in_student_view_data('pdf_report_url')

    def test_pb_answers_listed_in_pdf_report(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids)

        client = Client()
        response = client.get(self.pdf_report_url())
        self.assertEqual(response['Content-Type'], 'application/pdf')

        pdf_text = extract_text_from_pdf(response.content)

        self.assertNotIn('First Section', pdf_text)  # first section contains no pb-answers
        self.assertIn('Second Section', pdf_text)

        for data in expected_answers_data:
            self.assertIn(data['question'], pdf_text)
            self.assertIn(data['answer'], pdf_text)

    def test_pb_answers_not_listed_when_none_available_in_student_view(self):
        self.set_standard_scenario()
        self.go_to_view('student_view')
        self.fix_js_environment()

        self.assertRaises(
            NoSuchElementException,
            self.get_element_for_selected_pb_answers
        )

    def test_link_to_pb_answers_pdf_not_displayed_when_none_available_in_student_view(self):
        self.set_standard_scenario()
        self.go_to_view('student_view')
        self.fix_js_environment()

        self.assertRaises(
            NoSuchElementException,
            self.get_element_for_pb_answers_pdf_report
        )

    def test_all_metrics_hidden_by_default(self):
        self.configure_block()
        self.assertRaises(
            NoSuchElementException,
            self.get_element_for_course_progress
        )
        self.assertRaises(
            NoSuchElementException,
            self.get_element_for_course_proficiency
        )
        self.assertRaises(
            NoSuchElementException,
            self.get_element_for_course_engagement
        )
        self.assert_in_student_view_data('display_user_metrics', False)
        self.assert_not_in_student_view_user_state('progress')
        self.assert_not_in_student_view_user_state('proficiency')
        self.assert_not_in_student_view_user_state('engagement')

    def test_progress_metrics_listed_in_student_view_when_enabled(self):
        self.configure_block(display_metrics_section=True)

        element = self.get_element_for_course_progress()
        user_score = element.find_element_by_css_selector('span[data-progress-name="user"]')
        self.assertEqual(float(user_score.text), default_user_progress)

        cohort_score = element.find_element_by_css_selector('span[data-progress-name="cohort"]')
        self.assertEqual(float(cohort_score.text), default_cohort_average_progress)
        self.assert_in_student_view_data('display_user_metrics', True)
        self.assert_in_student_view_user_state('progress', {
            'cohort_average': default_cohort_average_progress,
            'user': default_user_progress,
        })

    def test_proficiency_metrics_listed_in_student_view_when_enabled(self):
        self.configure_block(display_metrics_section=True)

        element = self.get_element_for_course_proficiency()
        user_score = element.find_element_by_css_selector('span[data-proficiency-name="user"]')
        self.assertEqual(float(user_score.text), default_user_proficiency)

        cohort_score = element.find_element_by_css_selector('span[data-proficiency-name="cohort"]')
        self.assertEqual(float(cohort_score.text), default_cohort_average_proficiency)
        self.assert_in_student_view_data('display_user_metrics', True)
        self.assert_in_student_view_user_state('proficiency', {
            'cohort_average': default_cohort_average_proficiency,
            'user': default_user_proficiency,
        })

    def test_engagement_metrics_listed_in_student_view_when_enabled(self):
        self.configure_block(display_metrics_section=True)

        element = self.get_element_for_course_engagement()

        for key, value in default_engagement_metrics.iteritems():
            if key == 'user_score':
                tag = 'span[data-engagement-name="user"]'
            elif key == 'cohort_score':
                tag = 'span[data-engagement-name="cohort"]'
            else:
                tag = 'td[data-point-name="{}"]'.format(key)

            entry = element.find_element_by_css_selector(tag)
            self.assertEqual(float(entry.text), value)
        self.assert_in_student_view_data('display_user_metrics', True)
        self.assert_in_student_view_user_state('engagement', default_engagement_metrics)

    def test_pb_answer_blocks_selection_preserved_in_edit_view(self):
        selected_block_id = default_pb_answer_block_ids[1]
        self.configure_block(selected_pb_answer_blocks=[selected_block_id])
        # Reload edit view.
        self.go_to_view('studio_view')
        field_control = self.get_element_for_pb_answers_field()
        checked_checkboxes = field_control.find_elements_by_css_selector(
            'input[type=checkbox]:checked'
        )
        self.assertEqual(len(checked_checkboxes), 1)
        self.assertEqual(
            checked_checkboxes[0].get_attribute('value'),
            checkbox_value(selected_block_id)
        )

    def test_display_name(self):
        self.configure_block()
        title = self.element.find_element_by_css_selector('.title h3')
        # The default title is 'Course Journal'.
        default_title = 'Course Journal'
        self.assertEqual(title.text, default_title)
        self.assert_in_student_view_data('display_name', 'Course Journal')
        custom_title = 'My Custom Yournal'
        self.configure_block(display_name=custom_title)
        title = self.element.find_element_by_css_selector('.title h3')
        self.assertEqual(title.text, custom_title)
        self.assert_in_student_view_data('display_name', 'My Custom Yournal')

    def test_pdf_report_title(self):
        client = Client()
        custom_title = 'My Custom Report Title'

        # The default title is the course name.
        self.configure_block()
        response = client.get(self.pdf_report_url())
        self.assertEqual(response['Content-Type'], 'application/pdf')
        pdf_text = extract_text_from_pdf(response.content)

        self.assertIn(course_name, pdf_text)
        self.assertNotIn(custom_title, pdf_text)

        # Custom title testing
        self.configure_block(pdf_report_title=custom_title)
        response = client.get(self.pdf_report_url())
        self.assertEqual(response['Content-Type'], 'application/pdf')
        pdf_text = extract_text_from_pdf(response.content)

        self.assertIn(custom_title, pdf_text)
        self.assertNotIn(course_name, pdf_text)

    def test_no_takeaways_pdf_section_hidden_by_default(self):
        self.configure_block()

        self.assertRaises(
            NoSuchElementException,
            self.element.find_element_by_css_selector,
            '.eoc-key-takeaways'
        )
        self.assertNotIn('Takeaways PDF', self.element.text)
        self.assert_in_student_view_data('display_key_takeaways', False)

    def test_takeaways_pdf_enabled_but_not_configured(self):
        self.configure_block(display_key_takeaways_section=True)
        links = self.element.find_elements_by_css_selector('a.key-takeaways-link')
        self.assertEqual(len(links), 0)
        self.assertIn('Key Takeaways PDF not available at this time.', self.element.text)
        self.assert_in_student_view_data('display_key_takeaways', True)

    def test_takeaways_pdf_enabled_and_configured(self):
        self.configure_block(
            key_takeaways_pdf='/static/my.pdf',
            display_key_takeaways_section=True
        )
        link = self.element.find_element_by_css_selector('a.key-takeaways-link')
        self.assertIn('Key Takeaways', link.text)
        self.assertEqual(link.get_attribute('target'), '_blank')
        self.assert_in_student_view_data('display_key_takeaways', True)
        self.assert_in_student_view_data('key_takeaways_pdf_url', 'https://lms.base/static/my.pdf')

    def test_no_pdf_report_link_heading_configured(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids)
        heading = self.element.find_element_by_css_selector('.eoc-pdf-report .title')
        self.assertEqual('PDF Report', heading.text)
        self.assert_in_student_view_data('pdf_report_link_heading', 'PDF Report')

    def test_pdf_report_link_heading_configured(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids,
                             pdf_report_link_heading='My Report')
        heading = self.element.find_element_by_css_selector('.eoc-pdf-report .title')
        self.assertEqual('My Report', heading.text)
        self.assert_in_student_view_data('pdf_report_link_heading', 'My Report')

    def test_no_pdf_report_link_text_configured(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids)
        link = self.element.find_element_by_css_selector('a.pdf-report-link')
        self.assertEqual('Download PDF', link.text)
        self.assert_in_student_view_data('pdf_report_link_text', 'Download PDF')

    def test_pdf_report_link_text_configured(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids,
                             pdf_report_link_text='Download Report')
        link = self.element.find_element_by_css_selector('a.pdf-report-link')
        self.assertEqual('Download Report', link.text)
        self.assert_in_student_view_data('pdf_report_link_text', 'Download Report')

    def test_student_view_data(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(selected_pb_answer_blocks=selected_block_ids, )
        block = self.load_root_xblock()
        data = block.student_view_data()
        self.assertEqual(data, {
            'display_name': u'Course Journal',
            'display_key_takeaways': False,
            'pdf_report_link_heading': u'PDF Report',
            'pdf_report_url': u'https://lms.base/handler/test-scenario.eoc-journal.d0.u0/serve_pdf/?student=student_1',
            'pdf_report_link_text': u'Download PDF',
            'display_answers': False,
            'display_user_metrics': False
        })

    def test_student_view_user_state(self):
        selected_block_ids = default_pb_answer_block_ids[1:]
        self.configure_block(
            selected_pb_answer_blocks=selected_block_ids,
            display_key_takeaways_section=True,
            display_answers=True,
            display_metrics_section=True,
        )
        block = self.load_root_xblock()
        response = block.handle('student_view_user_state', None)
        data = json.loads(response.body)
        self.assertEqual(data, {
            'answer_sections': [{
                'name': u'Second Section',
                'questions': expected_answers_data,
            }],
            'progress': {
                'cohort_average': default_cohort_average_progress,
                'user': default_user_progress,
            },
            'proficiency': {
                'cohort_average': default_cohort_average_proficiency,
                'user': default_user_proficiency,
            },
            'engagement': default_engagement_metrics,
        })

    def assert_in_student_view_data(self, key, value=None):
        block = self.load_root_xblock()
        data = block.student_view_data()
        if value:
            self.assertEqual(data[key], value)
        else:
            self.assertIn(key, data)

    def assert_in_student_view_user_state(self, key, value=None):
        block = self.load_root_xblock()
        response = block.handle('student_view_user_state', None)
        data = json.loads(response.body)
        if value:
            self.assertEqual(data[key], value)
        else:
            self.assertIn(key, data)

    def assert_not_in_student_view_user_state(self, key):
        block = self.load_root_xblock()
        response = block.handle('student_view_user_state', None)
        data = json.loads(response.body)
        self.assertNotIn(key, data)

    def assert_not_in_student_view_data(self, key):
        block = self.load_root_xblock()
        data = block.student_view_data()
        self.assertNotIn(key, data)


class TestPdfGenerator(unittest.TestCase):
    def test_get_style_sheet_default_font(self):
        stylesheet = get_style_sheet()

        self.assertIsInstance(stylesheet, StyleSheet1)
        self.assertEqual(stylesheet['Normal'].fontName, canvas_basefontname)

    def test_get_style_sheet_custom_font(self):
        path = 'Vera.ttf'
        stylesheet = get_style_sheet(font_url=path)

        self.assertIsInstance(stylesheet, StyleSheet1)
        self.assertEqual(stylesheet['Normal'].fontName, 'customFont')
