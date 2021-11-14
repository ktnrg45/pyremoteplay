import logging
from textwrap import dedent

from OpenGL import GL
from PySide6 import QtGui, QtOpenGL
from PySide6.QtGui import QOpenGLFunctions, QSurfaceFormat
from PySide6.QtOpenGL import QOpenGLTexture
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from shiboken6 import VoidPtr

_LOGGER = logging.getLogger(__name__)

YUV_VERT = dedent("""
    #version 150 core
    uniform mat4 pos_matrix;
    uniform vec4 draw_pos;

    const vec2 verts[4] = vec2[] (
      vec2(-0.5,  0.5),
      vec2(-0.5, -0.5),
      vec2( 0.5,  0.5),
      vec2( 0.5, -0.5)
    );

    const vec2 texcoords[4] = vec2[] (
      vec2(0.0, 1.0),
      vec2(0.0, 0.0),
      vec2(1.0, 1.0),
      vec2(1.0, 0.0)
    );

    out vec2 v_coord;

    void main() {
       vec2 vert = verts[gl_VertexID];
       vec4 p = vec4((0.5 * draw_pos.z) + draw_pos.x + (vert.x * draw_pos.z),
                     (0.5 * draw_pos.w) + draw_pos.y + (vert.y * draw_pos.w),
                     0, 1);
       gl_Position = pos_matrix * p;
       v_coord = texcoords[gl_VertexID];
    }
""")


YUV_FRAG = dedent("""
    #version 150 core
    uniform sampler2D plane1;
    uniform sampler2D plane2;
    uniform sampler2D plane3;
    in vec2 v_coord;
    out vec4 out_color;

    void main() {
        vec3 yuv = vec3(
            (texture(plane1, v_coord).r - (16.0 / 255.0)) / ((235.0 - 16.0) / 255.0),
            (texture(plane2, v_coord).r - (16.0 / 255.0)) / ((240.0 - 16.0) / 255.0) - 0.5,
            (texture(plane3, v_coord).r - (16.0 / 255.0)) / ((240.0 - 16.0) / 255.0) - 0.5);
        vec3 rgb = mat3(
            1.0,        1.0,        1.0,
            0.0,        -0.21482,   2.12798,
            1.28033,    -0.38059,   0.0) * yuv;
        out_color = vec4(rgb, 1.0);
    }
""")


class YUVGLWidget(QOpenGLWidget, QOpenGLFunctions):
    TEXTURE_NAMES = ("plane1", "plane2", "plane3")

    def surface_format():
        defaultFormat = QSurfaceFormat.defaultFormat()
        defaultFormat.setProfile(QSurfaceFormat.CoreProfile)
        defaultFormat.setVersion(3, 3)
        QSurfaceFormat.setDefaultFormat(defaultFormat)
        return defaultFormat

    def __init__(self, width, height, surface_format=None, parent=None):
        QOpenGLWidget.__init__(self, parent)
        QOpenGLFunctions.__init__(self)
        if not surface_format:
            surface_format = YUVGLWidget.surface_format()
        self.setFormat(surface_format)
        self.surface_format = surface_format
        self.textures = []
        self.frameWidth = width
        self.frameHeight = height
        self.resize(self.frameWidth, self.frameHeight)
        self.program = QtOpenGL.QOpenGLShaderProgram(self)
        self.vao = QtOpenGL.QOpenGLVertexArrayObject()
        self.frame = self.draw_pos = None

    def __del__(self):
        self.makeCurrent()
        for texture in self.textures:
            texture.destroy()
        self.doneCurrent()

    def initializeGL(self):
        self.initializeOpenGLFunctions()

        # Setup shaders
        assert self.program.addShaderFromSourceCode(QtOpenGL.QOpenGLShader.Vertex, YUV_VERT)
        assert self.program.addShaderFromSourceCode(QtOpenGL.QOpenGLShader.Fragment, YUV_FRAG)

        self.program.link()
        self.program.bind()

        self.program.setUniformValue("draw_pos", 0, 0, self.width(), self.height())
        self.initializeTextures()

        self.vao.create()
        self.vao.bind()

    def paintGL(self):
        if not self.textures or not self.frame:
            return
        self.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        matrix = QtGui.QMatrix4x4()
        matrix.ortho(0, self.width(), self.height(), 0, 0.0, 100.0)

        self.program.setUniformValue("pos_matrix", matrix)

        self.glViewport(0, 0, self.width(), self.height())

        for index, plane in enumerate(self.frame.planes):
            self.bindPixelTexture(index, plane.to_bytes(), plane.line_size)

        self.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        if self.parent():
            self.parent().fps_update.emit()

    def initializeTextures(self):
        self.textures = []
        for index, name in enumerate(YUVGLWidget.TEXTURE_NAMES):
            width = self.frameWidth
            height = self.frameHeight
            if index > 0:
                width /= 2
                height /= 2

            texture = QOpenGLTexture(QOpenGLTexture.Target2D)
            texture.setFormat(QOpenGLTexture.R8_UNorm)
            texture.setSize(width, height)
            texture.allocateStorage(QOpenGLTexture.Red, QOpenGLTexture.UInt8)
            texture.setMinMagFilters(QOpenGLTexture.Linear, QOpenGLTexture.Linear)
            texture.setWrapMode(QOpenGLTexture.DirectionS, QOpenGLTexture.ClampToEdge)
            texture.setWrapMode(QOpenGLTexture.DirectionT, QOpenGLTexture.ClampToEdge)

            self.program.setUniformValue(name, index)
            self.program.setUniformValue1i(self.program.uniformLocation(name), index)
            self.textures.append(texture)

    def bindPixelTexture(self, index, pixels, stride):
        width = self.frameWidth if index == 0 else self.frameWidth / 2
        height = self.frameHeight if index == 0 else self.frameHeight / 2

        self.glActiveTexture(GL.GL_TEXTURE0 + index)
        texture = self.textures[index]
        texture.bind(GL.GL_TEXTURE0 + index)
        texture.setData(0, 0, 0, width, height, 0, QOpenGLTexture.Red, QOpenGLTexture.UInt8, VoidPtr(pixels))

    def next_video_frame(self, frame):
        self.frame = frame
        self.update()
