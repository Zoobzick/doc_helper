from django.test import RequestFactory, SimpleTestCase

from settings import error_views


class ErrorViewsTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/missing-page/")

    def test_page_not_found_renders_humorous_404(self):
        response = error_views.page_not_found(self.request)

        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response,
            "этой страницы нет даже в параллельной вселенной",
            status_code=404,
        )

    def test_permission_denied_renders_humorous_403(self):
        response = error_views.permission_denied(self.request)

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "ключи у администратора", status_code=403)

    def test_server_error_renders_humorous_500(self):
        response = error_views.server_error(self.request)

        self.assertEqual(response.status_code, 500)
        self.assertContains(
            response,
            "Сервер споткнулся и упал",
            status_code=500,
        )
