#!/usr/bin/env python
from decimal import Decimal
import logging
import re
from .pdfdevice import PDFTextDevice
from .pdffont import PDFUnicodeNotDefined
from .layout import LTContainer
from .layout import LTPage
from .layout import LTText
from .layout import LTLine
from .layout import LTRect
from .layout import LTCurve
from .layout import LTFigure
from .layout import LTImage
from .layout import LTChar
from .layout import LTTextLine
from .layout import LTTextBox
from .layout import LTTextBoxVertical
from .layout import LTTextGroup
from .utils import apply_matrix_pt
from .utils import mult_matrix
from .utils import enc
from .utils import bbox2str


##  PDFLayoutAnalyzer
##
class PDFLayoutAnalyzer(PDFTextDevice):

    def __init__(self, rsrcmgr, pageno=1, laparams=None):
        PDFTextDevice.__init__(self, rsrcmgr)
        self.pageno = pageno
        self.laparams = laparams
        self._stack = []
        return

    def begin_page(self, page, ctm):
        (x0, y0, x1, y1) = page.mediabox
        (x0, y0) = apply_matrix_pt(ctm, (x0, y0))
        (x1, y1) = apply_matrix_pt(ctm, (x1, y1))
        mediabox = (0, 0, abs(x0-x1), abs(y0-y1))
        self.cur_item = LTPage(self.pageno, mediabox)
        return

    def end_page(self, page):
        assert not self._stack
        assert isinstance(self.cur_item, LTPage)
        if self.laparams is not None:
            self.cur_item.analyze(self.laparams)
        self.pageno += 1
        self.receive_layout(self.cur_item)
        return

    def begin_figure(self, name, bbox, matrix):
        self._stack.append(self.cur_item)
        self.cur_item = LTFigure(name, bbox, mult_matrix(matrix, self.ctm))
        return

    def end_figure(self, _):
        fig = self.cur_item
        assert isinstance(self.cur_item, LTFigure)
        self.cur_item = self._stack.pop()
        self.cur_item.add(fig)
        return

    def render_image(self, name, stream):
        assert isinstance(self.cur_item, LTFigure)
        item = LTImage(name, stream,
                       (self.cur_item.x0, self.cur_item.y0,
                        self.cur_item.x1, self.cur_item.y1))
        self.cur_item.add(item)
        return

    def paint_path(self, gstate, stroke, fill, evenodd, path):
        shape = ''.join(x[0] for x in path)
        if shape == 'ml':
            # horizontal/vertical line
            (_, x0, y0) = path[0]
            (_, x1, y1) = path[1]
            (x0, y0) = apply_matrix_pt(self.ctm, (x0, y0))
            (x1, y1) = apply_matrix_pt(self.ctm, (x1, y1))
            if x0 == x1 or y0 == y1:
                self.cur_item.add(LTLine(gstate.linewidth, (x0, y0), (x1, y1)))
                return
        if shape == 'mlllh':
            # rectangle
            (_, x0, y0) = path[0]
            (_, x1, y1) = path[1]
            (_, x2, y2) = path[2]
            (_, x3, y3) = path[3]
            (x0, y0) = apply_matrix_pt(self.ctm, (x0, y0))
            (x1, y1) = apply_matrix_pt(self.ctm, (x1, y1))
            (x2, y2) = apply_matrix_pt(self.ctm, (x2, y2))
            (x3, y3) = apply_matrix_pt(self.ctm, (x3, y3))
            if ((x0 == x1 and y1 == y2 and x2 == x3 and y3 == y0) or
                (y0 == y1 and x1 == x2 and y2 == y3 and x3 == x0)):
                self.cur_item.add(LTRect(gstate.linewidth, (x0, y0, x2, y2)))
                return
        # other shapes
        pts = []
        for p in path:
            for i in xrange(1, len(p), 2):
                pts.append(apply_matrix_pt(self.ctm, (p[i], p[i+1])))
        self.cur_item.add(LTCurve(gstate.linewidth, pts))
        return

    def render_char(self, matrix, font, fontsize, scaling, rise, cid):
        try:
            text = font.to_unichr(cid)
            assert isinstance(text, unicode), text
        except PDFUnicodeNotDefined:
            text = self.handle_undefined_char(font, cid)
        textwidth = font.char_width(cid)
        textdisp = font.char_disp(cid)
        item = LTChar(matrix, font, fontsize, scaling, rise, text, textwidth, textdisp)
        self.cur_item.add(item)
        return item.adv

    def handle_undefined_char(self, font, cid):
        logging.info('undefined: %r, %r' % (font, cid))
        return '(cid:%d)' % cid

    def receive_layout(self, ltpage):
        return


##  PDFPageAggregator
##
class PDFPageAggregator(PDFLayoutAnalyzer):

    def __init__(self, rsrcmgr, pageno=1, laparams=None):
        PDFLayoutAnalyzer.__init__(self, rsrcmgr, pageno=pageno, laparams=laparams)
        self.result = None
        return

    def receive_layout(self, ltpage):
        self.result = ltpage
        return

    def get_result(self):
        return self.result


##  PDFConverter
##
class PDFConverter(PDFLayoutAnalyzer):

    def __init__(self, rsrcmgr, outfp, codec='utf-8', pageno=1, laparams=None):
        PDFLayoutAnalyzer.__init__(self, rsrcmgr, pageno=pageno, laparams=laparams)
        self.outfp = outfp
        self.codec = codec
        return


##  TextConverter
##
class TextConverter(PDFConverter):

    def __init__(self, rsrcmgr, outfp, codec='utf-8', pageno=1, laparams=None,
                 showpageno=False, imagewriter=None):
        PDFConverter.__init__(self, rsrcmgr, outfp, codec=codec, pageno=pageno, laparams=laparams)
        self.showpageno = showpageno
        self.imagewriter = imagewriter
        return

    def write_text(self, item):
        if hasattr(item, 'get_text'):
            text = item.get_text()
        else:
            text = item
        self.outfp.write(text.encode(self.codec, 'ignore'))
        return

    def receive_layout(self, ltpage):
        def render(item):
            if isinstance(item, LTContainer):
                for child in item:
                    render(child)
            elif isinstance(item, LTText):
                self.write_text(item)
            if isinstance(item, LTTextBox):
                self.write_text('\n')
            elif isinstance(item, LTImage):
                if self.imagewriter is not None:
                    self.imagewriter.export_image(item)
        if self.showpageno:
            self.write_text('Page %s\n' % ltpage.pageid)
        render(ltpage)
        self.write_text('\f')
        return

    # Some dummy functions to save memory/CPU when all that is wanted
    # is text.  This stops all the image and drawing ouput from being
    # recorded and taking up RAM.
    def render_image(self, name, stream):
        if self.imagewriter is None:
            return
        PDFConverter.render_image(self, name, stream)
        return

    def paint_path(self, gstate, stroke, fill, evenodd, path):
        return


class PositionAwareString(object):
    def __init__(self, renderable, codec):
        self.renderable = renderable
        self.codec = codec
        self.prev_str = None
        self.next_str = None
        self.is_indented = False

    def __repr__(self):
        return self.renderable

    def bytestring(self):
        return self.renderable.encode(self.codec, 'ignore')


class StringChain(list):
    def __init__(self, lst, outfp):
        list.__init__(self, lst)
        self.outfp = outfp
        last_word = None

    def most_common_size(self):
        """Statistical mode of the .size.

        Thanks to David Dao, http://stackoverflow.com/a/28129716/86209"""
        sizes = [w.size for w in self]
        return max(set(sizes), key=sizes.count)

    def markup(self):
        """Add Markdown bold/italic/header indicators"""

        self[-1].prev_str = self[-2]
        self.find_indents()
        self.set_boldness()
        self.set_headerness()
        self.escape()
      
        for w in self:
            if not w.prev_str:
                w.renderable = u'%s%s%s' % (w.headerness, w.boldness, w.renderable)
            elif not w.next_str:
                w.renderable = u'%s%s' % (w, w.boldness)
            else:
                if (w.boldness != w.prev_str.boldness) or (w.headerness != w.prev_str.headerness):
                    # close previous boldness
                    w.prev_str.renderable = u'%s%s' % (
                        w.prev_str.renderable.rstrip(), w.prev_str.boldness)
                    # open a new boldness
                    w.renderable = u'%s%s' % (w.boldness, w.renderable)
                if w.headerness != w.prev_str.headerness:
                    w.renderable = u'\n\n%s%s' % (w.headerness, w.renderable)

    def escape(self):
        for w in self:
            w.renderable = w.renderable.replace(u'*', u'\*')
        # also beginning # signs TODO

    def find_indents(self):
        
        self[0].indent = current_indent = 0
        for (idx, w) in enumerate(self[1:]):
            if w.x0:
                if (not current_indent) or ('\n' in self[idx-1].renderable):
                    current_indent = w.indent = w.x0
            w.indent = current_indent
                    
        least_indented = 1000000000
        col_start = 0
        for (idx, w) in enumerate(self):
            if w.y0 and w.prev_str and (w.y0 > w.prev_str.y0):
                # now we know the leftmost edge of this column, can set ``is_indented``
                for w2 in self[col_start:idx-1]:
                    w2.is_indented = (w2.indent > least_indented)
                least_indented = w.indent
                col_start = idx
        for y in self[col_start:idx-1]:
            y.is_indented = (y.indent > least_indented)
            
        self.anything_is_indented = max([w.is_indented for w in self])
        
    def set_boldness(self):
        for w in self:
            if 'bold' in w.fontname:
                w.boldness = u'**'
            elif 'italic' in w.fontname:
                w.boldness = u'*'
            else:
                w.boldness = u''

    def set_headerness(self):
        std_size = self.most_common_size()
        sizes = [w.size for w in self if w.size]
        max_size = max(sizes)
        most_common_size = max(set(sizes), key=sizes.count)
        next_size_up = max([s for s in sizes if s > most_common_size])
        
        for w in self:
            w.relative_header_score = 0
            if w.is_indented:
                w.relative_header_score += 1
            if w.size > std_size:
                w.relative_header_score += 1
            if next_size_up and (w.size > next_size_up):
                w.relative_header_score += 1
            if (w.size == max_size) and next_size_up and (max_size > next_size_up):
                w.relative_header_score += 1
                
        max_rel_rank = max(w.relative_header_score for w in self)
        min_rel_rank = min(w.relative_header_score for w in self)
        
        score_to_headerness_map = {}
        headerness = u'#'
        for rank in range(max_rel_rank, min_rel_rank, -1):
            score_to_headerness_map[rank] = u'%s ' % headerness
            headerness += '#'
            
        for w in self:
            w.headerness = score_to_headerness_map.get(w.relative_header_score, '')

    def write(self):
        for itm in self:
            self.outfp.write(itm.bytestring())


class MarkdownConverter(TextConverter):

    def __init__(self, rsrcmgr, outfp, **kwargs):
        TextConverter.__init__(self, rsrcmgr, outfp, **kwargs)
        self.string_chain = StringChain([], outfp)

    def write_text(self, item):
        """Store string with its font info into self.string_chain
           for later font analysis and ultimately output (but not yet)."""
        
        size = None
        fontname = ''
        try:
            text = item.get_text()
        except AttributeError:
            text = item
        try:
            (x0, y0) = (item.x0, item.y0)
        except AttributeError:
            (x0, y0) = (None, None)
        try:
            # round off font size so that we can find its mode later
            size = Decimal(item.size).quantize(Decimal('.001'))
            fontname = item.fontname.lower()
        except AttributeError:
            pass
        word = PositionAwareString(text, self.codec)
        word.size = size
        word.fontname = fontname
        word.x0 = x0
        word.y0 = y0
        if self.string_chain:
            word.prev_str = self.string_chain.last_word
        self.string_chain.append(word)
        if size:
            self.string_chain.last_word = word
            self.string_chain.last_word.next_str = word
        return


##  HTMLConverter
##
class HTMLConverter(PDFConverter):

    RECT_COLORS = {
        #'char': 'green',
        'figure': 'yellow',
        'textline': 'magenta',
        'textbox': 'cyan',
        'textgroup': 'red',
        'curve': 'black',
        'page': 'gray',
    }

    TEXT_COLORS = {
        'textbox': 'blue',
        'char': 'black',
    }

    def __init__(self, rsrcmgr, outfp, codec='utf-8', pageno=1, laparams=None,
                 scale=1, fontscale=1.0, layoutmode='normal', showpageno=True,
                 pagemargin=50, imagewriter=None, debug=0,
                 rect_colors={'curve': 'black', 'page': 'gray'},
                 text_colors={'char': 'black'}):
        PDFConverter.__init__(self, rsrcmgr, outfp, codec=codec, pageno=pageno, laparams=laparams)
        self.scale = scale
        self.fontscale = fontscale
        self.layoutmode = layoutmode
        self.showpageno = showpageno
        self.pagemargin = pagemargin
        self.imagewriter = imagewriter
        self.rect_colors = rect_colors
        self.text_colors = text_colors
        if debug:
            self.rect_colors.update(self.RECT_COLORS)
            self.text_colors.update(self.TEXT_COLORS)
        self._yoffset = self.pagemargin
        self._font = None
        self._fontstack = []
        self.write_header()
        return

    def write(self, text):
        self.outfp.write(text)
        return

    def write_header(self):
        self.write('<html><head>\n')
        self.write('<meta http-equiv="Content-Type" content="text/html; charset=%s">\n' % self.codec)
        self.write('</head><body>\n')
        return

    def write_footer(self):
        self.write('<div style="position:absolute; top:0px;">Page: %s</div>\n' %
                   ', '.join('<a href="#%s">%s</a>' % (i, i) for i in xrange(1, self.pageno)))
        self.write('</body></html>\n')
        return

    def write_text(self, text):
        self.write(enc(text, self.codec))
        return

    def place_rect(self, color, borderwidth, x, y, w, h):
        color = self.rect_colors.get(color)
        if color is not None:
            self.write('<span style="position:absolute; border: %s %dpx solid; '
                       'left:%dpx; top:%dpx; width:%dpx; height:%dpx;"></span>\n' %
                       (color, borderwidth,
                        x*self.scale, (self._yoffset-y)*self.scale,
                        w*self.scale, h*self.scale))
        return

    def place_border(self, color, borderwidth, item):
        self.place_rect(color, borderwidth, item.x0, item.y1, item.width, item.height)
        return

    def place_image(self, item, borderwidth, x, y, w, h):
        if self.imagewriter is not None:
            name = self.imagewriter.export_image(item)
            self.write('<img src="%s" border="%d" style="position:absolute; left:%dpx; top:%dpx;" '
                       'width="%d" height="%d" />\n' %
                       (enc(name), borderwidth,
                        x*self.scale, (self._yoffset-y)*self.scale,
                        w*self.scale, h*self.scale))
        return

    def place_text(self, color, text, x, y, size):
        color = self.text_colors.get(color)
        if color is not None:
            self.write('<span style="position:absolute; color:%s; left:%dpx; top:%dpx; font-size:%dpx;">' %
                       (color, x*self.scale, (self._yoffset-y)*self.scale, size*self.scale*self.fontscale))
            self.write_text(text)
            self.write('</span>\n')
        return

    def begin_div(self, color, borderwidth, x, y, w, h, writing_mode=False):
        self._fontstack.append(self._font)
        self._font = None
        self.write('<div style="position:absolute; border: %s %dpx solid; writing-mode:%s; '
                   'left:%dpx; top:%dpx; width:%dpx; height:%dpx;">' %
                   (color, borderwidth, writing_mode,
                    x*self.scale, (self._yoffset-y)*self.scale,
                    w*self.scale, h*self.scale))
        return

    def end_div(self, color):
        if self._font is not None:
            self.write('</span>')
        self._font = self._fontstack.pop()
        self.write('</div>')
        return

    def put_text(self, text, fontname, fontsize):
        font = (fontname, fontsize)
        if font != self._font:
            if self._font is not None:
                self.write('</span>')
            self.write('<span style="font-family: %s; font-size:%dpx">' %
                       (fontname, fontsize * self.scale * self.fontscale))
            self._font = font
        self.write_text(text)
        return

    def put_newline(self):
        self.write('<br>')
        return

    def receive_layout(self, ltpage):
        def show_group(item):
            if isinstance(item, LTTextGroup):
                self.place_border('textgroup', 1, item)
                for child in item:
                    show_group(child)
            return

        def render(item):
            if isinstance(item, LTPage):
                self._yoffset += item.y1
                self.place_border('page', 1, item)
                if self.showpageno:
                    self.write('<div style="position:absolute; top:%dpx;">' %
                               ((self._yoffset-item.y1)*self.scale))
                    self.write('<a name="%s">Page %s</a></div>\n' % (item.pageid, item.pageid))
                for child in item:
                    render(child)
                if item.groups is not None:
                    for group in item.groups:
                        show_group(group)
            elif isinstance(item, LTCurve):
                self.place_border('curve', 1, item)
            elif isinstance(item, LTFigure):
                self.begin_div('figure', 1, item.x0, item.y1, item.width, item.height)
                for child in item:
                    render(child)
                self.end_div('figure')
            elif isinstance(item, LTImage):
                self.place_image(item, 1, item.x0, item.y1, item.width, item.height)
            else:
                if self.layoutmode == 'exact':
                    if isinstance(item, LTTextLine):
                        self.place_border('textline', 1, item)
                        for child in item:
                            render(child)
                    elif isinstance(item, LTTextBox):
                        self.place_border('textbox', 1, item)
                        self.place_text('textbox', str(item.index+1), item.x0, item.y1, 20)
                        for child in item:
                            render(child)
                    elif isinstance(item, LTChar):
                        self.place_border('char', 1, item)
                        self.place_text('char', item.get_text(), item.x0, item.y1, item.size)
                else:
                    if isinstance(item, LTTextLine):
                        for child in item:
                            render(child)
                        if self.layoutmode != 'loose':
                            self.put_newline()
                    elif isinstance(item, LTTextBox):
                        self.begin_div('textbox', 1, item.x0, item.y1, item.width, item.height,
                                       item.get_writing_mode())
                        for child in item:
                            render(child)
                        self.end_div('textbox')
                    elif isinstance(item, LTChar):
                        self.put_text(item.get_text(), item.fontname, item.size)
                    elif isinstance(item, LTText):
                        self.write_text(item.get_text())
            return
        render(ltpage)
        self._yoffset += self.pagemargin
        return

    def close(self):
        self.write_footer()
        return


##  XMLConverter
##
class XMLConverter(PDFConverter):

    CONTROL = re.compile(ur'[\x00-\x08\x0b-\x0c\x0e-\x1f]')

    def __init__(self, rsrcmgr, outfp, codec='utf-8', pageno=1,
                 laparams=None, imagewriter=None, stripcontrol=False):
        PDFConverter.__init__(self, rsrcmgr, outfp, codec=codec, pageno=pageno, laparams=laparams)
        self.imagewriter = imagewriter
        self.stripcontrol = stripcontrol
        self.write_header()
        return

    def write_header(self):
        self.outfp.write('<?xml version="1.0" encoding="%s" ?>\n' % self.codec)
        self.outfp.write('<pages>\n')
        return

    def write_footer(self):
        self.outfp.write('</pages>\n')
        return

    def write_text(self, text):
        if self.stripcontrol:
            text = self.CONTROL.sub(u'', text)
        self.outfp.write(enc(text, self.codec))
        return

    def receive_layout(self, ltpage):
        def show_group(item):
            if isinstance(item, LTTextBox):
                self.outfp.write('<textbox id="%d" bbox="%s" />\n' %
                                 (item.index, bbox2str(item.bbox)))
            elif isinstance(item, LTTextGroup):
                self.outfp.write('<textgroup bbox="%s">\n' % bbox2str(item.bbox))
                for child in item:
                    show_group(child)
                self.outfp.write('</textgroup>\n')
            return

        def render(item):
            if isinstance(item, LTPage):
                self.outfp.write('<page id="%s" bbox="%s" rotate="%d">\n' %
                                 (item.pageid, bbox2str(item.bbox), item.rotate))
                for child in item:
                    render(child)
                if item.groups is not None:
                    self.outfp.write('<layout>\n')
                    for group in item.groups:
                        show_group(group)
                    self.outfp.write('</layout>\n')
                self.outfp.write('</page>\n')
            elif isinstance(item, LTLine):
                self.outfp.write('<line linewidth="%d" bbox="%s" />\n' %
                                 (item.linewidth, bbox2str(item.bbox)))
            elif isinstance(item, LTRect):
                self.outfp.write('<rect linewidth="%d" bbox="%s" />\n' %
                                 (item.linewidth, bbox2str(item.bbox)))
            elif isinstance(item, LTCurve):
                self.outfp.write('<curve linewidth="%d" bbox="%s" pts="%s"/>\n' %
                                 (item.linewidth, bbox2str(item.bbox), item.get_pts()))
            elif isinstance(item, LTFigure):
                self.outfp.write('<figure name="%s" bbox="%s">\n' %
                                 (item.name, bbox2str(item.bbox)))
                for child in item:
                    render(child)
                self.outfp.write('</figure>\n')
            elif isinstance(item, LTTextLine):
                self.outfp.write('<textline bbox="%s">\n' % bbox2str(item.bbox))
                for child in item:
                    render(child)
                self.outfp.write('</textline>\n')
            elif isinstance(item, LTTextBox):
                wmode = ''
                if isinstance(item, LTTextBoxVertical):
                    wmode = ' wmode="vertical"'
                self.outfp.write('<textbox id="%d" bbox="%s"%s>\n' %
                                 (item.index, bbox2str(item.bbox), wmode))
                for child in item:
                    render(child)
                self.outfp.write('</textbox>\n')
            elif isinstance(item, LTChar):
                self.outfp.write('<text font="%s" bbox="%s" size="%.3f">' %
                                 (enc(item.fontname), bbox2str(item.bbox), item.size))
                self.write_text(item.get_text())
                self.outfp.write('</text>\n')
            elif isinstance(item, LTText):
                self.outfp.write('<text>%s</text>\n' % item.get_text())
            elif isinstance(item, LTImage):
                if self.imagewriter is not None:
                    name = self.imagewriter.export_image(item)
                    self.outfp.write('<image src="%s" width="%d" height="%d" />\n' %
                                     (enc(name), item.width, item.height))
                else:
                    self.outfp.write('<image width="%d" height="%d" />\n' %
                                     (item.width, item.height))
            else:
                assert 0, item
            return
        render(ltpage)
        return

    def close(self):
        self.write_footer()
        return
