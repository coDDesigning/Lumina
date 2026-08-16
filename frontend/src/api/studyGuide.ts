export interface StudyGuide {
  id: number;
  course_id: number;
  content: string;
  created_at: string;
}

// Placeholder for study guide API which will be implemented later
export const studyGuideAPI = {
  generate: async (courseId: number): Promise<StudyGuide> => {
    // Placeholder implementation for UI testing
    return new Promise((resolve) => {
      setTimeout(() => {
        resolve({
          id: 1,
          course_id: courseId,
          content: 'This is a placeholder study guide.',
          created_at: new Date().toISOString(),
        });
      }, 1500);
    });
  },

  getForCourse: async (courseId: number): Promise<StudyGuide[]> => {
    return new Promise((resolve) => {
      setTimeout(() => {
        void courseId;
        resolve([]);
      }, 500);
    });
  },
};
