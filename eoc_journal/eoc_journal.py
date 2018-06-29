"""
An XBlock that allows learners to download their activity after they finish their course.
"""

from collections import OrderedDict
from io import BytesIO
import webob

from lxml import html
from lxml.html.clean import clean_html

from reportlab.lib import pagesizes
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from problem_builder.models import Answer
from xblock.core import XBlock
from xblock.fields import Scope, String, List
from xblock.fragment import Fragment
from xblockutils.resources import ResourceLoader
from xblockutils.studio_editable import StudioEditableXBlockMixin

from .api_client import ApiClient, calculate_engagement_score
from .course_blocks_api import CourseBlocksApiClient
from .utils import _, normalize_id

try:
    from django.contrib.auth.models import User
except ImportError:
    User = None  # pylint: disable=C0103


loader = ResourceLoader(__name__)


def provide_pb_answer_list(xblock_instance):
    """
    Returns a list of dicts containing information about pb-answer
    blocks present in current course.

    Used as a list value provider for the `selected_pb_answer_blocks`
    field in the Studio.
    """
    blocks = xblock_instance.list_pb_answers(all_blocks=True)
    result = []
    for block in blocks:
        block_name = block['display_name'] or block['name']
        label = ' / '.join([block['section'], block['subsection'], block['unit'], block_name])
        result.append({
            'display_name': label,
            'value': block['id'],
        })
    return result


@XBlock.needs('user')
class EOCJournalXBlock(StudioEditableXBlockMixin, XBlock):
    """
    An XBlock that allows learners to download their activity after they finish their course.
    """

    display_name = String(
        display_name=_("Title (display name)"),
        help=_("Title to display"),
        default=_("End of Course Journal"),
        scope=Scope.content,
    )

    key_takeaways_pdf = String(
        display_name=_("Key Takeaways PDF handle"),
        help=_(
            "URL handle of the Key Takeaways PDF file that was uploaded to Studio Files & Uploads section. "
            "Should start with '/static/'. Example: /static/KeyTakeaways.pdf"
        ),
        default="",
        scope=Scope.content,
    )

    selected_pb_answer_blocks = List(
        display_name=_("Problem Builder Freeform Answers"),
        help=_("Select Problem Builder Freeform Answer components which you want to include in the report."),
        default=[],
        scope=Scope.content,
        list_style='set',
        list_values_provider=provide_pb_answer_list,
    )

    editable_fields = (
        'display_name',
        'key_takeaways_pdf',
        'selected_pb_answer_blocks',
    )

    def student_view(self, context=None):
        """
        View shown to students.
        """
        context = context.copy() if context else {}

        context['display_name'] = self.display_name
        context["answer_sections"] = self.list_user_pb_answers_by_section()

        context["progress"] = self.get_progress_metrics()
        context["proficiency"] = self.get_proficiency_metrics()
        context["engagement"] = self.get_engagement_metrics()

        key_takeaways_handle = self.key_takeaways_pdf.strip()
        if key_takeaways_handle:
            context["key_takeaways_pdf_url"] = self._expand_static_url(self.key_takeaways_pdf)

        context["pdf_report_url"] = self.runtime.handler_url(self, "serve_pdf")

        fragment = Fragment()
        fragment.add_content(
            loader.render_template("templates/eoc_journal.html", context)
        )
        fragment.add_css_url(
            self.runtime.local_resource_url(self, "public/css/eoc_journal.css")
        )
        fragment.add_javascript_url(
            self.runtime.local_resource_url(self, "public/js/eoc_journal.js")
        )
        fragment.initialize_js('EOCJournalXBlock')
        return fragment

    @XBlock.handler
    def serve_pdf(self, request, _suffix):
        """
        Builds and serves a PDF document containing user's freeform answers.
        """
        styles = getSampleStyleSheet()
        pdf_buffer = BytesIO()
        document = SimpleDocTemplate(pdf_buffer, pagesize=pagesizes.letter, title=_("Report"))
        story = [
            Paragraph(self.display_name, styles["Title"]),
        ]

        answer_sections = self.list_user_pb_answers_by_section()
        for section in answer_sections:
            story.append(Spacer(0, 16))
            story.append(Paragraph(section["name"], styles["h1"]))
            for question in section["questions"]:
                story.append(Paragraph(question["question"], styles["h2"]))
                story.append(Paragraph(question["answer"], styles["Normal"]))

        document.build(story)
        pdf_buffer.seek(0)
        pdf = pdf_buffer.read()

        response = webob.Response(
            body=pdf,
            content_type='application/pdf',
        )

        return response

    def list_user_pb_answers_by_section(self):
        """
        Returns a list of dicts with pb-answers grouped by section.
        """
        # Get the selected blocks and their answers
        blocks = self.list_pb_answers()
        blocks = [b for b in blocks if b['id'] in self.selected_pb_answer_blocks]
        course_id = self._get_course_id()
        user_id = self._get_current_anonymous_user_id()
        answers_names = [block['name'] for block in blocks]

        answers = Answer.objects.filter(  # pylint: disable=no-member
            course_key=course_id,
            student_id=user_id,
            name__in=answers_names
        )

        if answers.count() > 0:
            # Map answer names to student inputs
            students_inputs = {a.name: a.student_input for a in answers}

            # Group answers by section
            answers = OrderedDict()

            for block in blocks:
                name = block['name']
                section = block['section']

                if section not in answers:
                    answers[section] = []

                parsed_question = html.fromstring(block['question'])
                question = clean_html(parsed_question).text_content()

                answers[section].append({
                    'answer': students_inputs.get(name, _('Not answered yet.')),
                    'question': question,
                })

            # Make list of sections-answers
            return [
                {'name': key, 'questions': value}
                for key, value in answers.items()
            ]
        else:
            return None

    def list_pb_answers(self, all_blocks=False):
        """
        Returns a list of dicts with info about all problem builder's pb-answer
        blocks present in the current coures.

        The items are ordered in the order they appear in the course.
        """
        response = self._fetch_pb_answer_blocks(all_blocks)
        blocks = response['blocks']
        course_block = blocks[response['root']]
        result = []
        for section_id in course_block.get('children', []):
            section_block = blocks[section_id]
            for subsection_id in section_block.get('children', []):
                subsection_block = blocks[subsection_id]
                for unit_id in subsection_block.get('children', []):
                    unit_block = blocks[unit_id]
                    for pb_answer_id in unit_block.get('children', []):
                        pb_answer_block = blocks[pb_answer_id]
                        result.append({
                            'section': section_block['display_name'],
                            'subsection': subsection_block['display_name'],
                            'unit': unit_block['display_name'],
                            'id': pb_answer_block['id'],
                            'name': pb_answer_block['student_view_data']['name'],
                            'question': pb_answer_block['student_view_data']['question'],
                            'display_name': pb_answer_block['display_name'],
                        })
        return result

    def _get_course_id(self):
        """
        Returns the course id (string) corresponding to the current course.
        """
        course_id = getattr(self.runtime, 'course_id', 'course_id')
        course_id = unicode(normalize_id(course_id))
        return course_id

    def _get_current_user(self):
        """
        Returns django.contrib.auth.models.User instance corresponding to the current user.
        """
        xblock_user = self.runtime.service(self, 'user').get_current_user()
        user_id = xblock_user.opt_attrs['edx-platform.user_id']
        user = User.objects.get(pk=user_id)  # pylint: disable=no-member
        return user

    def _get_current_anonymous_user_id(self):
        """
        Returns anonymous id (string) corresponding to the current user.
        """
        return self.runtime.anonymous_student_id

    def get_progress_metrics(self):
        """
        Fetches and returns dict with progress metrics for the current user
        in the course.
        """
        user = self._get_current_user()
        course_id = self._get_course_id()
        client = ApiClient(user, course_id)

        user_progress = client.get_user_progress()
        cohort_average = client.get_cohort_average_progress()

        if user_progress is None or cohort_average is None:
            return None

        return {
            'user': int(round(user_progress)),
            'cohort_average': int(round(cohort_average)),
        }

    def get_proficiency_metrics(self):
        """
        Fetches and returns dict with proficiency (grades) metrics for the current user
        in the course.
        """
        user = self._get_current_user()
        course_id = self._get_course_id()
        client = ApiClient(user, course_id)

        proficiency = client.get_user_proficiency()

        if proficiency is None:
            return None

        return {
            'user': int(round(proficiency.get('user', 0))),
            'cohort_average': int(round(proficiency.get('cohort_average', 0))),
            'graded_items': proficiency.get('graded_items', []),
        }

    def get_engagement_metrics(self):
        """
        Fetches and returns dict with engagement metrics for the current user
        and course.
        """
        user = self._get_current_user()
        course_id = self._get_course_id()
        client = ApiClient(user, course_id)

        user_engagement = client.get_user_engagement_metrics()
        course_engagement = client.get_cohort_engagement_metrics()

        if not course_engagement:
            return None
        else:
            course_point_sum = [
                calculate_engagement_score(metrics)
                for metrics in course_engagement['users'].itervalues()
            ]
            course_point_sum = sum(course_point_sum)

            enrollments = course_engagement['total_enrollments']

            if enrollments > 0:
                cohort_score = float(course_point_sum) / enrollments
            else:
                cohort_score = 0

            return {
                'user_score': int(round(calculate_engagement_score(user_engagement))),
                'cohort_score': int(round(cohort_score)),
                'new_posts': user_engagement.get('num_threads', 0),
                'total_replies': user_engagement.get('num_replies', 0) + user_engagement.get('num_comments', 0),
                'upvotes': user_engagement.get('num_upvotes', 0),
                'comments_generated': user_engagement.get('num_comments_generated', 0),
                'posts_followed': user_engagement.get('num_thread_followers', 0),
            }

    def _fetch_pb_answer_blocks(self, all_blocks=False):
        """
        Fetches blocks from the Course API. Results are currently limited to
        course, chapter, sequential, vetcial, and pb-answer blocks.

        If `all_blocks` is True, returns all blocks, including those that are
        visible only to specific learners (cohort groups, randomized content...).
        Only staff users can request `all_blocks`.
        """
        user = self._get_current_user()
        course_id = getattr(self.runtime, 'course_id', 'course_id')
        course_id = unicode(normalize_id(course_id))

        client = CourseBlocksApiClient(user, course_id)
        response = client.get_blocks(
            all_blocks=all_blocks,
            depth='all',
            requested_fields='student_view_data,children',
            student_view_data='pb-answer',
            block_types_filter='pb-answer,vertical,sequential,chapter,course',
            username=user.username,
        )
        return response

    def _expand_static_url(self, url):
        """
        This is required to make URLs like '/static/takeaways.pdf' work (note: that is the
        only portable URL format for static files that works across export/import and reruns).
        This method is unfortunately a bit hackish since XBlock does not provide a low-level API
        for this.
        """
        if hasattr(self.runtime, 'replace_urls'):
            url = self.runtime.replace_urls('"{}"'.format(url))[1:-1]
        elif hasattr(self.runtime, 'course_id'):
            # edX Studio uses a different runtime for 'studio_view' than 'student_view',
            # and the 'studio_view' runtime doesn't provide the replace_urls API.
            try:
                from static_replace import replace_static_urls  # pylint: disable=import-error
                url = replace_static_urls('"{}"'.format(url), None, course_id=self.runtime.course_id)[1:-1]
            except ImportError:
                pass
        return url
