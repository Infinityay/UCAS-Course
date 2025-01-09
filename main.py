from src.course_info_fetcher import CourseInfoFetcher

if __name__ == '__main__':
    fetcher = CourseInfoFetcher()
    fetcher.login_and_get_data()
    