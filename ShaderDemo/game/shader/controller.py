
import renpy
import renpy.display
import pygame_sdl2 as pygame
import random
import ctypes

from OpenGL import GL as gl

import shader
import rendering
import shadercode
import utils

class RenderContext(object):
    def __init__(self, renderer, w, h, time, shownTime, animationTime, uniforms, mousePos, events, store, overlayRender):
        self.renderer = renderer
        self.width = w
        self.height = h
        self.time = time
        self.shownTime = shownTime
        self.animationTime = animationTime
        self.uniforms = uniforms
        self.mousePos = mousePos
        self.events = events
        self.store = store
        self.continueRendering = True
        self.overlayRender = overlayRender
        self.overlayCanvas = None

    def createOverlayCanvas(self):
        if self.overlayCanvas is not None:
            return
        self.overlayCanvas = self.overlayRender.canvas()
        self.overlayCanvas.rect("#f00", (0, 0, self.width - 1, self.height - 1), 1)


class ControllerContext:
    def __init__(self):
        self.controller = None
        self.createCalled = False
        self.contextStore = {}
        self.modeChangeCount = 0
        self.delayFree = False
        self.persist = False
        self.updateModeChangeCount()

    def updateModeChangeCount(self):
        self.modeChangeCount = shader.getModeChangeCount()

    def freeController(self):
        if self.controller and self.modeChangeCount == shader.getModeChangeCount():
            self.controller.free()
        self.controller = None


class ControllerContextStore:
    def __init__(self):
        self.store = {}

    def get(self, tag):
        context = self.store.get(tag, None)
        if not context:
            context = ControllerContext()
            self.store[tag] = context

        #context.delayFree = False #Not really needed...

        return context

    def removeContext(self, tag):
        if tag in self.store:
            del self.store[tag]

    def getAllShaderDisplayables(self, displayType):
        displayables = []
        for disp in renpy.exports.scene_lists().get_all_displayables():
            try:
                disp.visit_all(lambda x: displayables.append(x))
            except AttributeError:
                #TODO child is sometimes None somewhere, we could do this manually...
                #Could renpy.showing(name, layer) work here?
                pass
        return [d for d in displayables if isinstance(d, displayType)]

    def checkDisplayableVisibility(self, displayType):
        tagged = {}
        for d in self.getAllShaderDisplayables(displayType):
            tagged[d.tag] = d

        removal = []
        for tag, context in self.store.items():
            if context.delayFree:
                removal.append((tag, context))
            elif not tag in tagged and not context.persist:
                #Not visible, free on next interaction
                context.delayFree = True

        for tag, context in removal:
            context.freeController()
            self.removeContext(tag)

        shader.log("Controller count: %s" % len(self.store))

    def _clear(self):
        #Usually there is no need to call this in normal use
        for tag, context in self.store.copy().items():
            context.freeController()
            self.removeContext(tag)
        self.store.clear()


class RenderController(object):
    def __init__(self):
        self.renderer = None
        self.frameBuffer = None

    def init(self, renderer):
        self.renderer = renderer

        w, h = self.renderer.getSize()
        self.frameBuffer = FrameBuffer(w, h, renderer.useDepth)

    def isValid(self):
        return self.renderer is not None

    def free(self):
        if self.renderer:
            self.renderer.free()
            self.renderer = None

        if self.frameBuffer:
            self.frameBuffer.free()
            self.frameBuffer = None

    def getSize(self):
        return self.renderer.getSize()

    def renderImage(self, context):
        width, height = self.getSize()
        gl.glViewport(0, 0, width, height)

        gl.glDisable(gl.GL_SCISSOR_TEST)

        gl.glEnable(gl.GL_ALPHA_TEST)
        gl.glAlphaFunc(gl.GL_GREATER, 0)

        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        self.frameBuffer.bind()

        self.renderer.render(context)

        self.frameBuffer.unbind()

        #TODO Restore blend state. Any other states that need restoring...?
        gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE_MINUS_SRC_ALPHA)

    def copyRenderBufferToSurface(self, surface):
        surface.lock()

        gl.glPixelStorei(gl.GL_PACK_ROW_LENGTH, surface.get_pitch() // surface.get_bytesize())

        gl.glBindTexture(gl.GL_TEXTURE_2D, self.frameBuffer.texture)
        gl.glGetTexImage(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, surface._pixels_address)

        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glPixelStorei(gl.GL_PACK_ROW_LENGTH, 0)

        surface.unlock()


class FrameBuffer:
    def __init__(self, width, height, depth=False):
        self.texture = self.createEmptyTexture(width, height)
        if self.texture == 0:
            raise RuntimeError("Can't create FrameBuffer textures")

        self.depthBuffer = 0
        if depth:
            self.depthBuffer = self.createDepthBuffer(width, height)
            if self.depthBuffer == 0:
                raise RuntimeError("Can't create FrameBuffer depth buffer")

        self.buffer = self.createFrameBuffer(self.texture, self.depthBuffer)
        if self.buffer == 0:
            raise RuntimeError("Can't create FrameBuffer buffer")

    def free(self):
        if self.texture:
            gl.glDeleteTextures(1, self.texture)
            self.texture = 0
        if self.depthBuffer:
            gl.glDeleteRenderbuffers(1, self.depthBuffer)
            self.depthBuffer = 0
        if self.buffer:
            gl.glDeleteFramebuffers(1, self.buffer)
            self.buffer = 0

    def createEmptyTexture(self, width, height):
        textureId = (gl.GLuint * 1)()
        gl.glGenTextures(1, textureId)
        gl.glBindTexture(gl.GL_TEXTURE_2D, textureId[0])
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        #None means reserve texture memory, but texels are undefined
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA8, width, height, 0, gl.GL_BGRA, gl.GL_UNSIGNED_BYTE, None)
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        return textureId[0]

    def createDepthBuffer(self, width, height):
        textureId = (gl.GLuint * 1)()
        gl.glGenRenderbuffers(1, textureId)
        gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, textureId[0])
        gl.glRenderbufferStorage(gl.GL_RENDERBUFFER, gl.GL_DEPTH_COMPONENT, width, height)
        gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, 0)
        return textureId[0]

    def createFrameBuffer(self, texture, depthBuffer):
        bufferId = (gl.GLuint * 1)()
        gl.glGenFramebuffers(1, bufferId);
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, bufferId[0])
        gl.glFramebufferTexture(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0, texture, 0)
        if depthBuffer:
            gl.glFramebufferRenderbuffer(gl.GL_FRAMEBUFFER, gl.GL_DEPTH_ATTACHMENT, gl.GL_RENDERBUFFER, depthBuffer);
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        return bufferId[0]

    def bind(self):
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.buffer)

    def unbind(self):
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)

